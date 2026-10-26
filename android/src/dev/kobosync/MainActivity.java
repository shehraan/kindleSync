package dev.kobosync;
import android.app.*;
import android.content.*;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.net.URI;
import java.io.File;

public final class MainActivity extends Activity {
    private TextView status, book, captions, captionTitle;
    private EditText server;
    private String configuredServer="";
    private String manifestJson="", captionKey="", captionMessage="";
    private JSONObject manifest;
    private CaptionTimeline timeline;
    private PlaybackService.LocalBinder playback;
    private boolean bound;
    private final ServiceConnection connection=new ServiceConnection() {
        public void onServiceConnected(ComponentName name,IBinder service) { playback=(PlaybackService.LocalBinder)service; render(); }
        public void onServiceDisconnected(ComponentName name) { playback=null; }
    };
    private final Handler handler = new Handler();
    private final Runnable refresh = new Runnable() { public void run() { render(); handler.postDelayed(this,250); } };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL);
        int pad=(int)(20*getResources().getDisplayMetrics().density); layout.setPadding(pad,pad,pad,pad);
        ScrollView scroll = new ScrollView(this); scroll.addView(layout); setContentView(scroll);
        TextView title=new TextView(this); title.setText("KoboSync"); title.setTextSize(28); layout.addView(title);
        TextView intro=new TextView(this); intro.setText("Your nightly reading, ready to listen.\nMorning handoff: 6 a.m. on your server.\n"); layout.addView(intro);
        server=new EditText(this); server.setHint("Server address, e.g. http://192.168.1.100:8787"); server.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
        configuredServer=SyncClient.prefs(this).getString("server","");
        server.setText(configuredServer); layout.addView(server);
        EditText token=new EditText(this); token.setHint("Pairing token"); token.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        token.setText(SyncClient.prefs(this).getString("token","")); layout.addView(token);
        Button save=new Button(this); save.setText("Save and sync"); layout.addView(save);
        status=new TextView(this); layout.addView(status);
        book=new TextView(this); book.setPadding(0,pad,0,pad); layout.addView(book);
        captionTitle=new TextView(this); captionTitle.setText("Captions"); layout.addView(captionTitle);
        captions=new TextView(this); captions.setTag("captions"); captions.setTextSize(20); captions.setMinLines(6);
        captions.setPadding(0,pad/2,0,pad); layout.addView(captions);
        Button play=new Button(this); play.setText("Resume offline audio"); layout.addView(play);
        play.setOnClickListener(v -> command("resume"));
        LinearLayout buttons=new LinearLayout(this); layout.addView(buttons);
        for (String action:new String[]{"back","toggle","forward"}) {
            Button b=new Button(this); b.setText(action.equals("back") ? "−30s" : action.equals("forward") ? "+30s" : "Play/pause"); buttons.addView(b,new LinearLayout.LayoutParams(0,-2,1));
            b.setOnClickListener(v -> command(action));
        }
        save.setOnClickListener(v -> {
            try {
                String address=server.getText().toString().trim().replaceAll("/+$","");
                URI uri=new URI(address);
                if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost()==null || uri.getUserInfo()!=null || (uri.getPath()!=null && !uri.getPath().isEmpty()) || uri.getQuery()!=null || uri.getFragment()!=null) throw new Exception("Use an http(s) server origin without a path");
                if (token.getText().toString().trim().isEmpty()) throw new Exception("Enter the pairing token");
                synchronized (SyncClient.PAIRING_LOCK) {
                    SyncClient.prefs(this).edit().putString("server",address).putString("token",token.getText().toString().trim()).commit();
                }
                configuredServer=address;
                SyncClient.schedule(this); save.setEnabled(false);
                new Thread(() -> {
                    try { SyncClient.sync(this,() -> false); }
                    catch (Exception e) { SyncClient.status(this,"Sync pending: " + e.getMessage()); }
                    runOnUiThread(() -> { save.setEnabled(true); render(); });
                }).start();
            } catch (Exception e) { status.setText(e.getMessage()); }
        });
        SyncClient.schedule(this);
        if (Build.VERSION.SDK_INT>=33) requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"},1);
    }
    private void render() {
        String savedServer=SyncClient.prefs(this).getString("server","");
        if (!savedServer.equals(configuredServer)) {
            if (server.getText().toString().equals(configuredServer)) server.setText(savedServer);
            configuredServer=savedServer;
        }
        text(status,SyncClient.prefs(this).getString("status","Pair your local server to begin."));
        try {
            String raw=SyncClient.prefs(this).getString("handoff","{}");
            if (!raw.equals(manifestJson)) { manifestJson=raw; manifest=new JSONObject(raw); }
            if (manifest==null || !manifest.has("position")) throw new JSONException("No handoff");
            text(book,manifest.getString("title") + "\nKobo saved position: " + manifest.getString("source_time"));
            renderCaptions();
        } catch (JSONException e) {
            text(book,"No offline handoff yet. Reading progress must reach the server before morning.");
            text(captions,"Captions will follow your offline audio.");
        }
    }
    private void renderCaptions() {
        try {
            PlaybackService.Snapshot snapshot=playback==null ? null : playback.snapshot();
            JSONObject current=snapshot==null ? manifest : snapshot.manifest;
            int track; long position;
            if (snapshot!=null) { track=snapshot.track; position=snapshot.positionMs; }
            else if (current.getString("revision").equals(SyncClient.prefs(this).getString("applied_revision",""))) {
                track=SyncClient.prefs(this).getInt("track",0); position=SyncClient.prefs(this).getLong("position_ms",0);
            } else { track=current.getJSONObject("position").getInt("track"); position=(long)(current.getJSONObject("position").getDouble("seconds")*1000); }
            // Caption metadata may be added/refreshed without changing the
            // resume revision. The same registered book has immutable audio.
            JSONObject captionManifest=current;
            if (current.getString("book_id").equals(manifest.getString("book_id"))) captionManifest=manifest;
            text(captionTitle,current.getString("book_id").equals(manifest.getString("book_id")) ? "Captions" : "Captions · " + current.getString("title"));
            JSONObject caption=SyncClient.captionForTrack(captionManifest,track);
            if (caption==null) { text(captions,"Sync to download captions for offline playback."); return; }
            File file=SyncClient.captionFile(this,caption.getString("sha256"));
            String key=file.getName()+":"+file.length()+":"+file.lastModified();
            if (!key.equals(captionKey)) {
                captionKey=key; timeline=null;
                captionMessage="Sync to download captions for offline playback.";
                if (file.isFile()) {
                    try { timeline=CaptionTimeline.load(file); }
                    catch (Exception e) { captionMessage="Captions could not be read. Try syncing again."; }
                }
            }
            text(captions,timeline==null ? captionMessage : timeline.windowAt(position));
        } catch (Exception e) { text(captions,"Captions are not available yet. Try syncing again."); }
    }
    private void text(TextView view,String value) { if (!view.getText().toString().equals(value)) view.setText(value); }
    private void command(String action) {
        startForegroundService(new Intent(this,PlaybackService.class).setAction(action));
        if (!bound) bound=bindService(new Intent(this,PlaybackService.class),connection,0);
    }
    @Override public void onStart() { super.onStart(); bound=bindService(new Intent(this,PlaybackService.class),connection,0); }
    @Override public void onStop() { if (bound) { unbindService(connection); bound=false; } playback=null; super.onStop(); }
    @Override public void onResume() { super.onResume(); handler.post(refresh); }
    @Override public void onPause() { handler.removeCallbacks(refresh); super.onPause(); }
}
