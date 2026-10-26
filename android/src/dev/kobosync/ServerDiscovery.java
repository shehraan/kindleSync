package dev.kobosync;

import java.io.ByteArrayOutputStream;
import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.net.InetSocketAddress;
import java.net.Proxy;
import java.net.Socket;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BooleanSupplier;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Finds a paired server after a private IPv4 address changes, without sending its token. */
public final class ServerDiscovery {
    private static final String PROTOCOL = "kobosync-discovery-v1";
    private static final int CONFIGURED_TIMEOUT_MS = 2000;
    private static final int PEER_TIMEOUT_MS = 350;
    private static final int SCAN_TIMEOUT_MS = 8500;
    private static final int MAX_RESPONSE_BYTES = 1024;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final ThreadFactory THREADS = runnable -> {
        Thread thread = new Thread(runnable, "kobosync-discovery");
        thread.setDaemon(true);
        return thread;
    };

    private ServerDiscovery() { }

    /** Non-private, hostname and HTTPS origins are returned without probing. */
    public static String resolve(String base, String token, BooleanSupplier cancelled) throws Exception {
        validateToken(token);
        Origin origin = origin(base);
        checkCancelled(cancelled);
        if (!origin.privateHttp) return base;
        return resolveCandidates(origin.canonical, token, cancelled, candidates(origin.canonical));
    }

    // Public resolve restricts origins and generates the private /24; tests use only loopback here.
    // At most two 2-second configured probes surround the bounded 8.5-second scan.
    static String resolveCandidates(String configured, String token, BooleanSupplier cancelled,
            List<String> candidates) throws Exception {
        if (probe(configured, token, cancelled, CONFIGURED_TIMEOUT_MS)) return configured;
        String found = find(candidates, token, cancelled, PEER_TIMEOUT_MS, SCAN_TIMEOUT_MS);
        if (found != null) return found;
        if (probe(configured, token, cancelled, CONFIGURED_TIMEOUT_MS)) return configured;
        throw new IOException("Cannot find your paired KoboSync server on this local network. "
                + "Keep the computer awake and connected to the same Wi-Fi.");
    }

    // Also used by JVM tests to verify the subnet without making network requests.
    static List<String> candidates(String base) throws Exception {
        Origin origin = origin(base);
        if (!origin.privateHttp) return Collections.emptyList();
        int lastDot = origin.host.lastIndexOf('.');
        String prefix = origin.host.substring(0, lastDot + 1);
        String port = origin.port == -1 ? "" : ":" + origin.port;
        List<String> result = new ArrayList<>(253);
        for (int last = 1; last <= 254; last++) {
            String peer = "http://" + prefix + last + port;
            if (!peer.equals(origin.canonical)) result.add(peer);
        }
        return result;
    }

    // A real HTTP probe, separated so JVM tests can use loopback instead of scanning a LAN.
    static boolean probe(String origin, String token, BooleanSupplier cancelled, int timeoutMs) throws Exception {
        validateToken(token);
        Origin parsed = origin(origin);
        checkCancelled(cancelled);
        if (timeoutMs <= 0) throw new IllegalArgumentException("Probe timeout must be positive");
        try (Session session = new Session(cancelled)) {
            return session.probe(parsed.canonical, token, timeoutMs);
        }
    }

    // Production supplies only candidates() results; JVM tests supply real loopback origins.
    static String find(List<String> candidates, String token, BooleanSupplier cancelled,
            int peerTimeoutMs, long scanTimeoutMs) throws Exception {
        validateToken(token);
        checkCancelled(cancelled);
        if (peerTimeoutMs <= 0 || scanTimeoutMs <= 0) {
            throw new IllegalArgumentException("Discovery timeouts must be positive");
        }
        long start = System.nanoTime();
        long budget = TimeUnit.MILLISECONDS.toNanos(scanTimeoutMs);
        List<Future<String>> futures = new ArrayList<>(candidates.size());
        try (Session session = new Session(cancelled)) {
            CompletionService<String> completed = new ExecutorCompletionService<>(session.workers);
            try {
                for (String candidate : candidates) {
                    checkCancelled(cancelled);
                    futures.add(completed.submit(() -> session.probe(candidate, token, peerTimeoutMs) ? candidate : null));
                }
                int remaining = futures.size();
                while (remaining > 0) {
                    checkCancelled(cancelled);
                    long left = budget - (System.nanoTime() - start);
                    if (left <= 0) break;
                    Future<String> future;
                    try {
                        future = completed.poll(Math.min(left, TimeUnit.MILLISECONDS.toNanos(40)), TimeUnit.NANOSECONDS);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        InterruptedIOException error = new InterruptedIOException("Server discovery cancelled");
                        error.initCause(interrupted);
                        throw error;
                    }
                    if (future == null) continue;
                    remaining--;
                    String found;
                    try {
                        found = future.get();
                    } catch (ExecutionException failure) {
                        if (failure.getCause() instanceof Exception) throw (Exception) failure.getCause();
                        throw new IOException("Cannot verify local server discovery", failure.getCause());
                    }
                    checkCancelled(cancelled);
                    if (found != null) return found;
                }
                checkCancelled(cancelled);
                return null;
            } finally {
                for (Future<String> future : futures) future.cancel(true);
            }
        }
    }

    private static Origin origin(String base) throws IOException {
        String message = "Enter a valid HTTP or HTTPS server origin, such as http://192.168.1.49:8787";
        if (base == null || base.isEmpty() || !base.equals(base.trim())) throw new IOException(message);
        try {
            URI uri = new URI(base);
            String scheme = uri.getScheme();
            String host = uri.getHost();
            if (!("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme))
                    || host == null || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                    || uri.getRawFragment() != null || uri.getPort() == 0 || uri.getPort() > 65535
                    || uri.getRawAuthority().endsWith(":")
                    || !(uri.getRawPath().isEmpty() || "/".equals(uri.getRawPath()))) {
                throw new IOException(message);
            }
            int[] ip = ipv4(host);
            boolean privateIp = ip != null && (ip[0] == 10 || (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31)
                    || (ip[0] == 192 && ip[1] == 168));
            String canonical = ("http".equalsIgnoreCase(scheme) ? "http" : "https") + "://" + uri.getRawAuthority();
            return new Origin(canonical, host, uri.getPort(), privateIp && "http".equalsIgnoreCase(scheme));
        } catch (URISyntaxException malformed) {
            throw new IOException(message, malformed);
        }
    }

    private static int[] ipv4(String host) {
        String[] pieces = host.split("\\.", -1);
        if (pieces.length != 4) return null;
        int[] result = new int[4];
        for (int i = 0; i < 4; i++) {
            String piece = pieces[i];
            if (!piece.matches("0|[1-9][0-9]{0,2}")) return null;
            result[i] = Integer.parseInt(piece);
            if (result[i] > 255) return null;
        }
        return result;
    }

    private static void validateToken(String token) throws IOException {
        if (token == null || token.trim().isEmpty()) throw new IOException("Enter your already-paired KoboSync token");
    }

    private static void checkCancelled(BooleanSupplier cancelled) throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted() || cancelled.getAsBoolean()) {
            throw new InterruptedIOException("Server discovery cancelled");
        }
    }

    private static String hex(byte[] bytes) {
        char[] result = new char[bytes.length * 2];
        char[] digits = "0123456789abcdef".toCharArray();
        for (int i = 0; i < bytes.length; i++) {
            result[i * 2] = digits[(bytes[i] & 255) >>> 4];
            result[i * 2 + 1] = digits[bytes[i] & 15];
        }
        return new String(result);
    }

    private static boolean verified(byte[] body, String nonce, String token) throws Exception {
        for (byte value : body) if (value < 0) return false;
        String[] lines = new String(body, StandardCharsets.US_ASCII).split("\n", -1);
        if (lines.length != 5 || !PROTOCOL.equals(lines[0]) || !lines[1].matches("[a-f0-9]{32}")
                || !lines[2].matches("[a-f0-9]{32}") || !lines[3].matches("[a-f0-9]{64}")
                || !lines[4].isEmpty()) return false;
        if (!MessageDigest.isEqual(nonce.getBytes(StandardCharsets.US_ASCII), lines[1].getBytes(StandardCharsets.US_ASCII))) {
            return false;
        }
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(token.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        byte[] expected = mac.doFinal((PROTOCOL + "\n" + nonce + "\n" + lines[2]).getBytes(StandardCharsets.US_ASCII));
        byte[] proof = new byte[32];
        for (int i = 0; i < proof.length; i++) {
            proof[i] = (byte) Integer.parseInt(lines[3].substring(i * 2, i * 2 + 2), 16);
        }
        return MessageDigest.isEqual(expected, proof);
    }

    private static String readHeader(InputStream input, int[] remaining) throws IOException {
        StringBuilder line = new StringBuilder();
        while (--remaining[0] >= 0) {
            int value = input.read();
            if (value == '\r') {
                if (--remaining[0] < 0 || input.read() != '\n') throw new IOException("Malformed discovery HTTP header");
                return line.toString();
            }
            if (value < 0 || value > 127 || (value < 32 && value != '\t')) {
                throw new IOException("Malformed discovery HTTP header");
            }
            line.append((char) value);
        }
        throw new IOException("Oversized discovery HTTP headers");
    }

    private static byte[] readResponse(InputStream input, BooleanSupplier cancelled) throws IOException {
        int[] headerBudget = {8192};
        if (!readHeader(input, headerBudget).matches("HTTP/1\\.[01] 200(?: .*)?")) return null;
        long length = -1;
        String line;
        while (!(line = readHeader(input, headerBudget)).isEmpty()) {
            checkCancelled(cancelled);
            int colon = line.indexOf(':');
            if (colon <= 0) throw new IOException("Malformed discovery HTTP header");
            String name = line.substring(0, colon).toLowerCase(Locale.ROOT);
            String value = line.substring(colon + 1).trim();
            if ("content-length".equals(name)) {
                if (length != -1 || !value.matches("[0-9]{1,10}")) throw new IOException("Invalid discovery response length");
                length = Long.parseLong(value);
                if (length > MAX_RESPONSE_BYTES) return null;
            }
            // HTTP/1.0 requests use a length or connection-close body, never chunked coding.
            if ("transfer-encoding".equals(name)
                    || ("content-encoding".equals(name) && !"identity".equalsIgnoreCase(value))) return null;
        }
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        byte[] buffer = new byte[256];
        while (length < 0 || body.size() < length) {
            checkCancelled(cancelled);
            int wanted = Math.min(buffer.length, MAX_RESPONSE_BYTES + 1 - body.size());
            if (length >= 0) wanted = Math.min(wanted, (int) length - body.size());
            int count = input.read(buffer, 0, wanted);
            if (count == -1) {
                if (length >= 0) throw new IOException("Incomplete discovery response");
                break;
            }
            body.write(buffer, 0, count);
            if (body.size() > MAX_RESPONSE_BYTES) return null;
        }
        return body.toByteArray();
    }

    private static void close(Socket socket) {
        try { socket.close(); }
        catch (IOException ignored) { }
    }

    private static final class Session implements AutoCloseable {
        final ExecutorService workers = Executors.newFixedThreadPool(16, THREADS);
        final ScheduledThreadPoolExecutor timer = new ScheduledThreadPoolExecutor(1, THREADS);
        final Set<Socket> active = Collections.newSetFromMap(new ConcurrentHashMap<Socket, Boolean>());
        final AtomicBoolean closed = new AtomicBoolean();
        final BooleanSupplier cancelled;

        Session(BooleanSupplier cancelled) {
            this.cancelled = cancelled;
            timer.setRemoveOnCancelPolicy(true);
        }

        boolean probe(String origin, String token, int timeoutMs) throws Exception {
            checkCancelled(cancelled);
            Origin target = origin(origin);
            if (!target.canonical.startsWith("http://") || ipv4(target.host) == null) {
                throw new IOException("Discovery probes require an HTTP IPv4 origin");
            }
            byte[] random = new byte[16];
            RANDOM.nextBytes(random);
            String nonce = hex(random);
            Socket socket = new Socket(Proxy.NO_PROXY);
            active.add(socket);
            ScheduledFuture<?> deadline = null;
            ScheduledFuture<?> cancellation = null;
            try {
                if (closed.get()) throw new InterruptedIOException("Server discovery cancelled");
                // Closing the raw socket interrupts reads even when a peer trickles bytes.
                // HTTP URLConnection input-stream close can instead wait for its read lock.
                deadline = timer.schedule(() -> ServerDiscovery.close(socket), timeoutMs, TimeUnit.MILLISECONDS);
                Thread owner = Thread.currentThread();
                cancellation = timer.scheduleWithFixedDelay(() -> {
                    if (owner.isInterrupted() || cancelled.getAsBoolean()) ServerDiscovery.close(socket);
                }, 40, 40, TimeUnit.MILLISECONDS);
                socket.connect(new InetSocketAddress(target.host, target.port == -1 ? 80 : target.port), timeoutMs);
                socket.setSoTimeout(timeoutMs);
                String request = "GET /v1/discovery?nonce=" + nonce + " HTTP/1.0\r\nHost: "
                        + target.canonical.substring(7) + "\r\nConnection: close\r\nAccept: text/plain\r\n\r\n";
                socket.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));
                byte[] body = readResponse(new BufferedInputStream(socket.getInputStream(), 1024), cancelled);
                checkCancelled(cancelled);
                return body != null && verified(body, nonce, token);
            } catch (IOException unavailable) {
                checkCancelled(cancelled);
                return false;
            } finally {
                if (deadline != null) deadline.cancel(false);
                if (cancellation != null) cancellation.cancel(false);
                active.remove(socket);
                ServerDiscovery.close(socket);
            }
        }

        @Override public void close() {
            closed.set(true);
            workers.shutdownNow();
            for (Socket socket : active) ServerDiscovery.close(socket);
            timer.shutdownNow();
        }
    }

    private static final class Origin {
        final String canonical;
        final String host;
        final int port;
        final boolean privateHttp;

        Origin(String canonical, String host, int port, boolean privateHttp) {
            this.canonical = canonical;
            this.host = host;
            this.port = port;
            this.privateHttp = privateHttp;
        }
    }
}
