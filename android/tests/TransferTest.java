package dev.kobosync;

import com.sun.net.httpserver.HttpServer;
import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.concurrent.atomic.AtomicBoolean;

public class TransferTest {
    public static void main(String[] args) throws Exception {
        byte[] bytes = "an entire offline audiobook track".getBytes("UTF-8");
        String sha = Transfer.hash(bytes);
        AtomicBoolean resumed = new AtomicBoolean(false);
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/track", exchange -> {
            if (!"Bearer test".equals(exchange.getRequestHeaders().getFirst("Authorization"))) {
                exchange.sendResponseHeaders(401, -1); exchange.close(); return;
            }
            int start = 0;
            String range = exchange.getRequestHeaders().getFirst("Range");
            if (range != null) { start = Integer.parseInt(range.substring(6, range.length()-1)); resumed.set(true); }
            if (start > 0) exchange.getResponseHeaders().add("Content-Range", "bytes " + start + "-" + (bytes.length-1) + "/" + bytes.length);
            exchange.sendResponseHeaders(start > 0 ? 206 : 200, bytes.length-start);
            exchange.getResponseBody().write(bytes, start, bytes.length-start); exchange.close();
        });
        server.start();
        Path dir = Files.createTempDirectory("kobosync-transfer-test");
        try {
            URL url = new URL("http://127.0.0.1:" + server.getAddress().getPort() + "/track");
            File target = dir.resolve("audio.mp3").toFile();
            Files.write(dir.resolve("audio.mp3.part"), "an entire ".getBytes("UTF-8"));
            Transfer.fetch(url, "test", target, bytes.length, sha, () -> false);
            if (!resumed.get() || !java.util.Arrays.equals(Files.readAllBytes(target.toPath()), bytes)) throw new AssertionError("Resume failed");
            File bad = dir.resolve("bad.mp3").toFile();
            try { Transfer.fetch(url, "test", bad, bytes.length, "0".repeat(64), () -> false); throw new AssertionError("Wrong checksum accepted"); }
            catch (IOException expected) { if (bad.exists()) throw new AssertionError("Corrupt audio became visible"); }
            System.out.println("Transfer tests passed: authenticated resume and corrupt-file rejection");
        } finally { server.stop(0); try (var files = Files.list(dir)) { files.forEach(p -> p.toFile().delete()); } Files.delete(dir); }
    }
}
