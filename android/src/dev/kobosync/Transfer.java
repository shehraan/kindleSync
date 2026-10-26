package dev.kobosync;

import java.io.*;
import java.net.*;
import java.security.*;
import java.util.function.BooleanSupplier;

/** Resumable transfer. A final filename exists only after size/hash verification. */
public final class Transfer {
    private static String hex(byte[] bytes) {
        StringBuilder b = new StringBuilder();
        for (byte value : bytes) b.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        return b.toString();
    }
    public static String hash(byte[] bytes) throws Exception {
        return hex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    public static boolean valid(File file, long size, String sha) throws Exception {
        if (!file.isFile() || file.length() != size) return false;
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[262144]; int n;
            while ((n=in.read(buffer)) != -1) md.update(buffer, 0, n);
        }
        return hex(md.digest()).equals(sha);
    }
    public static void fetch(URL url, String token, File target, long size, String sha, BooleanSupplier cancelled) throws Exception {
        if (!sha.matches("[a-f0-9]{64}") || size < 1) throw new IOException("Invalid audio manifest");
        if (valid(target, size, sha)) return;
        File part = new File(target.getPath()+".part");
        if (part.length() >= size) part.delete();
        long offset = part.length();
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setInstanceFollowRedirects(false);
        c.setConnectTimeout(15000); c.setReadTimeout(30000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        if (offset > 0) c.setRequestProperty("Range", "bytes="+offset+"-");
        try {
            int status = c.getResponseCode();
            if (status != 200 && status != 206) throw new IOException("Audio server returned " + status);
            if (status == 200) offset = 0;
            else if (!c.getHeaderField("Content-Range").startsWith("bytes " + offset + "-")) throw new IOException("Invalid resumed transfer range");
            try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(part, offset>0)) {
                byte[] buffer = new byte[262144]; int n; long received = offset;
                while ((n=in.read(buffer)) != -1) {
                    if (cancelled.getAsBoolean()) throw new InterruptedIOException("Download deferred; will resume");
                    received += n;
                    if (received > size) throw new IOException("Audio exceeds manifest size");
                    out.write(buffer,0,n);
                }
            }
            if (!valid(part,size,sha)) { part.delete(); throw new IOException("Audio checksum/length mismatch; retry download"); }
            if (target.exists() && !target.delete()) throw new IOException("Cannot replace cached audio");
            if (!part.renameTo(target)) throw new IOException("Cannot finish audio download");
        } finally { c.disconnect(); }
    }
}
