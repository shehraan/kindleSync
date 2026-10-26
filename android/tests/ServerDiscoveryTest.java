package dev.kobosync;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class ServerDiscoveryTest {
    private static final String TOKEN = "paired-秘密-token";
    private static final String INSTANCE = "0123456789abcdef0123456789abcdef";

    public static void main(String[] args) throws Exception {
        int failures = 0;
        failures += run("origins outside private HTTP are unchanged", ServerDiscoveryTest::nonPrivateOrigins);
        failures += run("invalid origins and empty tokens", ServerDiscoveryTest::invalidInputs);
        failures += run("private /24 candidate boundaries", ServerDiscoveryTest::candidates);
        failures += run("real proof and unauthenticated probe", ServerDiscoveryTest::authenticatedDiscovery);
        failures += run("wrong proofs and malformed responses", ServerDiscoveryTest::invalidResponses);
        failures += run("redirects are not followed", ServerDiscoveryTest::redirects);
        failures += run("verified candidate selection", ServerDiscoveryTest::selection);
        failures += run("cancellation and active scan cleanup", ServerDiscoveryTest::cancellation);
        failures += run("scan deadline and concurrency bound", ServerDiscoveryTest::scanDeadline);
        failures += run("slow response has a peer deadline", ServerDiscoveryTest::peerDeadline);
        failures += run("configured server tolerates a slow proof", ServerDiscoveryTest::slowConfiguredServer);
        failures += run("configured server is retried after transient failure", ServerDiscoveryTest::transientConfiguredFailure);
        failures += run("verified scan result skips configured retry", ServerDiscoveryTest::scanResultSkipsRetry);
        failures += run("exhausted retry never accepts an unverified server", ServerDiscoveryTest::exhaustedConfiguredRetry);
        failures += run("configured probes cancel promptly", ServerDiscoveryTest::configuredCancellation);
        if (failures != 0) throw new AssertionError(failures + " discovery test groups failed");
        System.out.println("ServerDiscovery tests passed: 15 groups");
    }

    private static void nonPrivateOrigins() throws Exception {
        for (String base : new String[] {"http://8.8.8.8:8866", "http://example.invalid:8866",
                "https://192.168.7.213:8866/", "http://127.0.0.1:1", "http://172.32.0.1", "http://[::1]:8866"}) {
            equal(base, ServerDiscovery.resolve(base, TOKEN, () -> false), "unchanged origin");
            if (!ServerDiscovery.candidates(base).isEmpty()) throw new AssertionError("scanned " + base);
        }
    }

    private static void invalidInputs() throws Exception {
        for (String base : new String[] {null, "", "192.168.7.213:8866", "ftp://192.168.7.213",
                "http://192.168.7.213/api", "http://user:secret@192.168.7.213", "http://192.168.7.213/?x=1",
                "http://192.168.7.213/#fragment", "http://192.168.7.213:0", "http://192.168.7.213:70000",
                "http://192.168.7.213:", " http://192.168.7.213", "http://999.168.7.213"}) {
            try {
                ServerDiscovery.resolve(base, TOKEN, () -> false);
                throw new AssertionError("accepted invalid origin: " + base);
            } catch (IOException expected) {
                if (expected.getMessage() == null || expected.getMessage().isEmpty()) {
                    throw new AssertionError("origin error needs an explanation");
                }
            }
        }
        for (String token : new String[] {null, "", " \t "}) {
            try {
                ServerDiscovery.resolve("https://example.invalid", token, () -> false);
                throw new AssertionError("accepted empty pairing token");
            } catch (IOException expected) { }
        }
    }

    private static void candidates() throws Exception {
        List<String> peers = ServerDiscovery.candidates("http://192.168.7.213:8866/");
        Set<String> unique = new HashSet<>(peers);
        if (peers.size() != 253 || unique.size() != 253) throw new AssertionError("wrong private host count");
        if (!unique.contains("http://192.168.7.1:8866") || !unique.contains("http://192.168.7.254:8866")) {
            throw new AssertionError("missing subnet endpoints");
        }
        for (String peer : peers) {
            if (!peer.matches("http://192\\.168\\.7\\.[0-9]{1,3}:8866")
                    || peer.equals("http://192.168.7.0:8866") || peer.equals("http://192.168.7.255:8866")
                    || peer.equals("http://192.168.7.213:8866")) throw new AssertionError("unsafe candidate " + peer);
        }
        for (String base : new String[] {"http://10.3.4.5", "http://172.16.4.5", "http://172.31.4.5"}) {
            if (ServerDiscovery.candidates(base).size() != 253) throw new AssertionError("missed RFC1918 range");
        }
    }

    private static void authenticatedDiscovery() throws Exception {
        AtomicReference<String> failure = new AtomicReference<>();
        Set<String> nonces = new HashSet<>();
        try (TestServer server = new TestServer(exchange -> {
            String query = exchange.getRequestURI().getRawQuery();
            if (!"GET".equals(exchange.getRequestMethod()) || !"/v1/discovery".equals(exchange.getRequestURI().getPath())
                    || query == null || !query.matches("nonce=[a-f0-9]{32}")) failure.set("wrong probe request");
            if (exchange.getRequestHeaders().getFirst("Authorization") != null) failure.set("sent Authorization");
            for (List<String> values : exchange.getRequestHeaders().values()) {
                for (String value : values) if (value.contains(TOKEN)) failure.set("sent pairing token in header");
            }
            if (exchange.getRequestBody().read() != -1) failure.set("sent a probe body");
            String nonce = query == null ? "" : query.substring(6);
            synchronized (nonces) { nonces.add(nonce); }
            reply(exchange, 200, response(nonce, INSTANCE, TOKEN));
        })) {
            if (!ServerDiscovery.probe(server.origin(), TOKEN, () -> false, 400)
                    || !ServerDiscovery.probe(server.origin(), TOKEN, () -> false, 400)) {
                throw new AssertionError("valid paired proof rejected");
            }
            if (failure.get() != null) throw new AssertionError(failure.get());
            if (nonces.size() != 2) throw new AssertionError("probe nonce was reused");
        }
    }

    private static void invalidResponses() throws Exception {
        AtomicInteger mode = new AtomicInteger();
        try (TestServer server = new TestServer(exchange -> {
            String nonce = exchange.getRequestURI().getRawQuery().substring(6);
            String body = response(nonce, INSTANCE, TOKEN);
            switch (mode.get()) {
                case 0: body = response(nonce, INSTANCE, "wrong token"); break;
                case 1: body = response("ffffffffffffffffffffffffffffffff", INSTANCE, TOKEN); break;
                case 2: body = body.replace(INSTANCE, "not-an-instance"); break;
                case 3: body = body + "extra\n"; break;
                case 4: body = body.substring(0, body.length() - 1); break;
                case 5: body = body.replace("\n", "\r\n"); break;
                case 6: body = body.replace("kobosync-discovery-v1", "kobosync-discovery-v2"); break;
                case 7: body = body.replace(INSTANCE, "é123456789abcdef0123456789abcdef"); break;
                case 8: body = repeat('x', 2048); break;
                case 9:
                    byte[] bytes = repeat('x', 2048).getBytes(StandardCharsets.US_ASCII);
                    exchange.sendResponseHeaders(200, 0);
                    exchange.getResponseBody().write(bytes);
                    exchange.close();
                    return;
                case 10: reply(exchange, 401, body); return;
                default: throw new AssertionError("unexpected fixture");
            }
            reply(exchange, 200, body);
        })) {
            for (int i = 0; i <= 10; i++) {
                mode.set(i);
                if (ServerDiscovery.probe(server.origin(), TOKEN, () -> false, 400)) {
                    throw new AssertionError("accepted invalid response " + i);
                }
            }
        }
    }

    private static void redirects() throws Exception {
        AtomicInteger followed = new AtomicInteger();
        try (TestServer target = new TestServer(exchange -> {
            followed.incrementAndGet();
            reply(exchange, 200, "unexpected redirect");
        }); TestServer redirect = new TestServer(exchange -> {
            exchange.getResponseHeaders().add("Location", target.origin() + "/v1/discovery");
            exchange.sendResponseHeaders(302, -1);
            exchange.close();
        })) {
            if (ServerDiscovery.probe(redirect.origin(), TOKEN, () -> false, 400)) {
                throw new AssertionError("redirect accepted as a paired server");
            }
            if (followed.get() != 0) throw new AssertionError("followed discovery redirect");
        }
    }

    private static void selection() throws Exception {
        try (TestServer wrong = new TestServer(exchange -> reply(exchange, 200,
                response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, "wrong")));
             TestServer right = new TestServer(exchange -> reply(exchange, 200,
                response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, TOKEN)))) {
            equal(right.origin(), ServerDiscovery.find(Arrays.asList(wrong.origin(), right.origin()),
                    TOKEN, () -> false, 400, 1500), "verified candidate");
            if (ServerDiscovery.find(Arrays.asList(wrong.origin()), TOKEN, () -> false, 400, 1500) != null) {
                throw new AssertionError("unpaired server selected");
            }
        }
    }

    private static void cancellation() throws Exception {
        AtomicInteger requests = new AtomicInteger();
        CountDownLatch started = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicBoolean cancelled = new AtomicBoolean();
        try (TestServer server = new TestServer(exchange -> {
            requests.incrementAndGet();
            started.countDown();
            release.await(3, TimeUnit.SECONDS);
            exchange.close();
        })) {
            try {
                ServerDiscovery.probe(server.origin(), TOKEN, () -> true, 400);
                throw new AssertionError("pre-cancelled probe ran");
            } catch (InterruptedIOException expected) { }
            if (requests.get() != 0) throw new AssertionError("cancelled probe sent HTTP");
            Thread canceller = new Thread(() -> {
                try { started.await(2, TimeUnit.SECONDS); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
                cancelled.set(true);
            });
            canceller.start();
            long start = System.nanoTime();
            try {
                ServerDiscovery.find(Arrays.asList(server.origin()), TOKEN, cancelled::get, 2000, 3000);
                throw new AssertionError("active cancellation was ignored");
            } catch (InterruptedIOException expected) {
                if (elapsedMs(start) > 1200) throw new AssertionError("active cancellation was too slow");
            } finally {
                release.countDown();
                canceller.join(2500);
            }
        } finally { release.countDown(); }
    }

    private static void scanDeadline() throws Exception {
        AtomicInteger active = new AtomicInteger();
        AtomicInteger maximum = new AtomicInteger();
        CountDownLatch release = new CountDownLatch(1);
        try (TestServer server = new TestServer(exchange -> {
            int count = active.incrementAndGet();
            maximum.accumulateAndGet(count, Math::max);
            try { release.await(3, TimeUnit.SECONDS); }
            finally { active.decrementAndGet(); exchange.close(); }
        })) {
            List<String> candidates = new ArrayList<>();
            for (int i = 0; i < 40; i++) candidates.add(server.origin());
            long start = System.nanoTime();
            try {
                if (ServerDiscovery.find(candidates, TOKEN, () -> false, 2000, 250) != null) {
                    throw new AssertionError("unanswered peer selected");
                }
                if (elapsedMs(start) > 1400) throw new AssertionError("scan exceeded its overall deadline");
                if (maximum.get() < 2 || maximum.get() > 16) throw new AssertionError("invalid concurrency " + maximum);
            } finally { release.countDown(); }
        } finally { release.countDown(); }
    }

    private static void peerDeadline() throws Exception {
        try (TestServer server = new TestServer(exchange -> {
            exchange.sendResponseHeaders(200, 0);
            try {
                for (int i = 0; i < 20; i++) {
                    exchange.getResponseBody().write('x');
                    exchange.getResponseBody().flush();
                    Thread.sleep(60);
                }
            } finally { exchange.close(); }
        })) {
            long start = System.nanoTime();
            if (ServerDiscovery.probe(server.origin(), TOKEN, () -> false, 200)) {
                throw new AssertionError("incomplete trickling proof accepted");
            }
            if (elapsedMs(start) > 900) throw new AssertionError("trickling response bypassed peer timeout");
        }
    }

    private static void slowConfiguredServer() throws Exception {
        AtomicInteger configuredRequests = new AtomicInteger();
        AtomicInteger scanRequests = new AtomicInteger();
        try (TestServer configured = new TestServer(exchange -> {
            configuredRequests.incrementAndGet();
            Thread.sleep(650);
            reply(exchange, 200, response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, TOKEN));
        }); TestServer peer = new TestServer(exchange -> {
            scanRequests.incrementAndGet();
            reply(exchange, 503, "not ready");
        })) {
            equal(configured.origin(), ServerDiscovery.resolveCandidates(configured.origin(), TOKEN,
                    () -> false, Arrays.asList(peer.origin())), "slow configured server");
            if (configuredRequests.get() != 1 || scanRequests.get() != 0) {
                throw new AssertionError("slow configured server unnecessarily triggered discovery");
            }
        }
    }

    private static void transientConfiguredFailure() throws Exception {
        AtomicInteger configuredRequests = new AtomicInteger();
        AtomicBoolean sentAuthorization = new AtomicBoolean();
        List<String> order = Collections.synchronizedList(new ArrayList<>());
        try (TestServer configured = new TestServer(exchange -> {
            int request = configuredRequests.incrementAndGet();
            order.add("configured-" + request);
            if (exchange.getRequestHeaders().getFirst("Authorization") != null) sentAuthorization.set(true);
            if (request == 1) reply(exchange, 503, "temporarily busy");
            else {
                Thread.sleep(550);
                reply(exchange, 200, response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, TOKEN));
            }
        }); TestServer peer = new TestServer(exchange -> {
            order.add("scan");
            if (exchange.getRequestHeaders().getFirst("Authorization") != null) sentAuthorization.set(true);
            reply(exchange, 200, response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, "wrong token"));
        })) {
            equal(configured.origin(), ServerDiscovery.resolveCandidates(configured.origin(), TOKEN,
                    () -> false, Arrays.asList(peer.origin())), "recovered configured server");
            equal("[configured-1, scan, configured-2]", order.toString(), "discovery order");
            if (sentAuthorization.get()) throw new AssertionError("workflow sent Authorization before verification");
        }
    }

    private static void scanResultSkipsRetry() throws Exception {
        AtomicInteger configuredRequests = new AtomicInteger();
        try (TestServer configured = new TestServer(exchange -> {
            configuredRequests.incrementAndGet();
            reply(exchange, 503, "unavailable");
        }); TestServer peer = new TestServer(exchange -> reply(exchange, 200,
                response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, TOKEN)))) {
            equal(peer.origin(), ServerDiscovery.resolveCandidates(configured.origin(), TOKEN,
                    () -> false, Arrays.asList(peer.origin())), "verified replacement server");
            if (configuredRequests.get() != 1) throw new AssertionError("retried despite a verified replacement");
        }
    }

    private static void exhaustedConfiguredRetry() throws Exception {
        AtomicInteger configuredRequests = new AtomicInteger();
        try (TestServer configured = new TestServer(exchange -> {
            configuredRequests.incrementAndGet();
            reply(exchange, 200, response(exchange.getRequestURI().getRawQuery().substring(6), INSTANCE, "wrong"));
        }); TestServer peer = new TestServer(exchange -> reply(exchange, 503, "unavailable"))) {
            try {
                ServerDiscovery.resolveCandidates(configured.origin(), TOKEN, () -> false, Arrays.asList(peer.origin()));
                throw new AssertionError("returned an origin without a paired proof");
            } catch (IOException expected) { }
            if (configuredRequests.get() != 2) throw new AssertionError("configured server was not retried exactly once");
        }
    }

    private static void configuredCancellation() throws Exception {
        configuredCancellation(false);
        configuredCancellation(true);
    }

    private static void configuredCancellation(boolean duringRetry) throws Exception {
        AtomicInteger configuredRequests = new AtomicInteger();
        AtomicBoolean cancelled = new AtomicBoolean();
        CountDownLatch waiting = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        try (TestServer configured = new TestServer(exchange -> {
            int request = configuredRequests.incrementAndGet();
            if (duringRetry && request == 1) {
                reply(exchange, 503, "temporarily busy");
                return;
            }
            waiting.countDown();
            release.await(3, TimeUnit.SECONDS);
            exchange.close();
        })) {
            Thread canceller = new Thread(() -> {
                try {
                    if (waiting.await(2, TimeUnit.SECONDS)) cancelled.set(true);
                } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
            });
            canceller.start();
            long start = System.nanoTime();
            try {
                ServerDiscovery.resolveCandidates(configured.origin(), TOKEN, cancelled::get, Collections.emptyList());
                throw new AssertionError("configured probe ignored cancellation");
            } catch (InterruptedIOException expected) {
                if (elapsedMs(start) > 1000) throw new AssertionError("configured cancellation was too slow");
            } finally {
                release.countDown();
                canceller.interrupt();
                canceller.join(2500);
            }
        } finally { release.countDown(); }
    }

    private static String response(String nonce, String instance, String token) throws Exception {
        String signed = "kobosync-discovery-v1\n" + nonce + "\n" + instance;
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(token.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        StringBuilder hex = new StringBuilder();
        for (byte value : mac.doFinal(signed.getBytes(StandardCharsets.US_ASCII))) {
            hex.append(String.format("%02x", value & 255));
        }
        return signed + "\n" + hex + "\n";
    }

    private static void reply(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        try { exchange.getResponseBody().write(bytes); }
        finally { exchange.close(); }
    }

    private static String repeat(char value, int count) {
        char[] chars = new char[count];
        Arrays.fill(chars, value);
        return new String(chars);
    }

    private static long elapsedMs(long start) { return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start); }

    private static void equal(String expected, String actual, String context) {
        if (!expected.equals(actual)) throw new AssertionError(context + ": expected " + expected + " but got " + actual);
    }

    private static int run(String name, CheckedTest test) {
        try { test.run(); return 0; }
        catch (Exception | AssertionError failure) {
            System.err.println("FAIL " + name + ": " + failure);
            return 1;
        }
    }

    private interface CheckedTest { void run() throws Exception; }
    private interface Handler { void handle(HttpExchange exchange) throws Exception; }

    private static final class TestServer implements AutoCloseable {
        final HttpServer server;
        final ExecutorService executor = Executors.newCachedThreadPool();

        TestServer(Handler handler) throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.setExecutor(executor);
            server.createContext("/", exchange -> {
                try { handler.handle(exchange); }
                catch (Exception failure) { exchange.close(); }
            });
            server.start();
        }

        String origin() { return "http://127.0.0.1:" + server.getAddress().getPort(); }

        @Override public void close() {
            server.stop(0);
            executor.shutdownNow();
        }
    }
}
