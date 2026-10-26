package dev.kobosync;

import android.content.*;
import android.app.job.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.function.BooleanSupplier;

public final class SyncClient {
    static final Object PAIRING_LOCK=new Object();
    public static SharedPreferences prefs(Context c) { return c.getSharedPreferences("kobosync", Context.MODE_PRIVATE); }
    public static void status(Context c, String text) { prefs(c).edit().putString("status", text).apply(); }
    public static void schedule(Context c) {
        JobInfo job = new JobInfo.Builder(601, new ComponentName(c, SyncJob.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED)
            .setPeriodic(15*60*1000L).setPersisted(true)
            .setBackoffCriteria(30000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).build();
        c.getSystemService(JobScheduler.class).schedule(job);
    }
    private static JSONObject request(String base, String token, String path, JSONObject body) throws Exception {
        if (base.isEmpty()) throw new IOException("Enter your server address and pairing token");
        HttpURLConnection c = (HttpURLConnection) new URL(base+path).openConnection();
        c.setInstanceFollowRedirects(false);
        c.setConnectTimeout(15000); c.setReadTimeout(30000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        try {
            if (body != null) {
                c.setRequestMethod("POST"); c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = c.getOutputStream()) { out.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
            }
            int response = c.getResponseCode();
            if (response == 204) return null;
            if (response != 200) throw new IOException(response == 401 ? "Pairing token rejected" : "Server returned " + response);
            try (InputStream in = c.getInputStream(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                byte[] b = new byte[4096]; int n;
                while ((n=in.read(b)) != -1) { bytes.write(b,0,n); if (bytes.size()>2000000) throw new IOException("Oversized server response"); }
                return new JSONObject(new String(bytes.toByteArray(),StandardCharsets.UTF_8));
            }
        } finally { c.disconnect(); }
    }
    public static File trackFile(Context c, JSONObject track) throws Exception {
        String hash = track.getString("sha256");
        if (!hash.matches("[a-f0-9]{64}")) throw new IOException("Invalid audio identity");
        File dir = new File(c.getFilesDir(),"audio"); dir.mkdirs();
        return new File(dir, hash+".audio");
    }
    public static File captionFile(Context c,String hash) throws IOException {
        if (!hash.matches("[a-f0-9]{64}")) throw new IOException("Invalid caption identity");
        File dir=new File(c.getFilesDir(),"captions"); dir.mkdirs();
        return new File(dir,hash+".vtt");
    }
    public static JSONObject captionForTrack(JSONObject manifest,int track) {
        JSONArray captions=manifest.optJSONArray("captions");
        if (captions!=null) for (int i=0;i<captions.length();i++) {
            JSONObject caption=captions.optJSONObject(i);
            if (caption!=null && caption.optInt("track",-1)==track) return caption;
        }
        return null;
    }
    private static void cache(Context c, String base, String token, JSONObject manifest, BooleanSupplier cancelled) throws Exception {
        JSONArray tracks = manifest.getJSONArray("tracks");
        if (tracks.length() == 0 || tracks.length()>1000) throw new IOException("Invalid track count");
        for (int i=0; i<tracks.length(); i++) {
            JSONObject t = tracks.getJSONObject(i);
            String path = t.getString("url");
            if (!path.matches("/v1/audio/[A-Za-z0-9_-]{1,64}/[0-9]{1,4}")) throw new IOException("Unexpected audio URL");
            status(c,"Downloading " + manifest.getString("title") + " · track " + (i+1) + "/" + tracks.length());
            URL url = new URL(base+path);
            Transfer.fetch(url,token,trackFile(c,t),t.getLong("bytes"),t.getString("sha256"),cancelled);
        }
        JSONArray captions=manifest.optJSONArray("captions");
        if (captions!=null) {
            if (captions.length()!=tracks.length()) throw new IOException("Missing track captions");
            boolean[] seen=new boolean[tracks.length()];
            for (int i=0;i<captions.length();i++) {
                JSONObject caption=captions.getJSONObject(i);
                int track=caption.getInt("track");
                if (track<0 || track>=tracks.length() || seen[track]) throw new IOException("Invalid caption track");
                seen[track]=true;
                String path=caption.getString("url"), hash=caption.getString("sha256");
                if (!path.matches("/v1/captions/[A-Za-z0-9_-]{1,64}/[a-f0-9]{64}") || !path.endsWith("/"+hash)) throw new IOException("Unexpected caption URL");
                File file=captionFile(c,hash);
                status(c,"Downloading captions · track " + (track+1) + "/" + tracks.length());
                Transfer.fetch(new URL(base+path),token,file,caption.getLong("bytes"),hash,cancelled);
                CaptionTimeline.load(file);
            }
        }
    }
    private static void checkPairing(SharedPreferences preferences,String base,String token) throws IOException {
        if (!base.equals(preferences.getString("server","")) || !token.equals(preferences.getString("token",""))) throw new IOException("Pairing settings changed; sync again");
    }
    public static synchronized void sync(Context c, BooleanSupplier cancelled) throws Exception {
        SharedPreferences preferences=prefs(c);
        final String configured, token;
        synchronized (PAIRING_LOCK) {
            configured=preferences.getString("server",""); token=preferences.getString("token","");
        }
        if (configured.isEmpty() || token.isEmpty()) throw new IOException("Enter your server address and pairing token");
        final String resolved=ServerDiscovery.resolve(configured,token,cancelled);
        synchronized (PAIRING_LOCK) {
            checkPairing(preferences,configured,token);
            if (!resolved.equals(configured) && !preferences.edit().putString("server",resolved).commit()) throw new IOException("Cannot save server address");
        }
        JSONObject handoff = request(resolved,token,"/v1/handoff",null);
        if (handoff != null) {
            cache(c,resolved,token,handoff,cancelled);
            // Durable preference update publishes only a fully downloaded handoff.
            synchronized (PAIRING_LOCK) {
                checkPairing(preferences,resolved,token);
                if (!preferences.edit().putString("handoff",handoff.toString()).commit()) throw new IOException("Cannot store offline handoff");
            }
            request(resolved,token,"/v1/ready",new JSONObject().put("revision",handoff.getString("revision")));
            status(c,"Offline audio ready: " + handoff.getString("title"));
        }
        synchronized (PAIRING_LOCK) { checkPairing(preferences,resolved,token); }
        JSONObject next = request(resolved,token,"/v1/prepare",null);
        if (next != null && (handoff == null || !next.getString("book_id").equals(handoff.getString("book_id")))) {
            cache(c,resolved,token,next,cancelled);
            status(c,"Audio preloaded. Waiting for the morning resume point.");
        } else if (handoff == null) status(c,"Waiting for Kobo progress and the morning handoff.");
    }
}
