package dev.kobosync;

import android.app.*;
import android.content.*;
import android.media.session.*;
import android.media.MediaMetadata;
import android.service.notification.StatusBarNotification;
import android.os.*;
import android.net.*;
import android.view.View;
import android.widget.TextView;
import org.json.*;
import java.io.*;
import java.net.URL;
import java.util.function.BooleanSupplier;

/** Runs inside the real Android process, against its media stack and storage. */
public final class PlaybackSmoke extends Instrumentation {
    private Bundle arguments;
    private Activity activity;
    private volatile PlaybackService.LocalBinder localPlayback;
    private boolean serviceBound;
    private final ServiceConnection playbackConnection=new ServiceConnection() {
        public void onServiceConnected(ComponentName name,IBinder service) { localPlayback=(PlaybackService.LocalBinder)service; }
        public void onServiceDisconnected(ComponentName name) { localPlayback=null; }
    };

    private long playerPosition() {
        final long[] position={-1};
        runOnMainSync(() -> {
            PlaybackService.Snapshot snapshot=localPlayback==null ? null : localPlayback.snapshot();
            if (snapshot!=null) position[0]=snapshot.positionMs;
        });
        return position[0];
    }
    private void releaseBinding() {
        if (serviceBound) runOnMainSync(() -> { getTargetContext().unbindService(playbackConnection); serviceBound=false; localPlayback=null; });
    }

    @Override public void onCreate(Bundle args) { arguments=args; start(); }

    private void require(boolean condition,String message) {
        if (!condition) throw new AssertionError(message);
    }
    private void await(BooleanSupplier condition,String message) {
        long deadline=SystemClock.elapsedRealtime()+60000;
        while (SystemClock.elapsedRealtime()<deadline) {
            if (condition.getAsBoolean()) return;
            SystemClock.sleep(100);
        }
        throw new AssertionError(message);
    }
    private MediaController controller() {
        Context context=getTargetContext();
        for (StatusBarNotification posted:context.getSystemService(NotificationManager.class).getActiveNotifications()) {
            MediaSession.Token token=posted.getNotification().extras.getParcelable(Notification.EXTRA_MEDIA_SESSION);
            if (token!=null) {
                MediaController c=new MediaController(context,token);
                if (c.getPackageName().equals("dev.kobosync")) return c;
            }
        }
        return null;
    }
    private void startPlayer() {
        runOnMainSync(() -> getTargetContext().startForegroundService(new Intent(getTargetContext(),PlaybackService.class).setAction("resume")));
    }
    private void pausePlayer() {
        MediaController c=controller(); require(c!=null,"Missing media session");
        c.getTransportControls().pause();
        await(() -> c.getPlaybackState()!=null && c.getPlaybackState().getState()==PlaybackState.STATE_PAUSED,"Media pause did not take effect");
    }
    private MediaController playingController() {
        await(() -> {
            MediaController c=controller();
            return c!=null && c.getPlaybackState()!=null && c.getPlaybackState().getState()==PlaybackState.STATE_PLAYING;
        },"Audio never entered the playing state");
        return controller();
    }

    private String captionText() {
        final String[] text={""};
        runOnMainSync(() -> {
            View view=activity.getWindow().getDecorView().findViewWithTag("captions");
            if (view instanceof TextView) text[0]=((TextView)view).getText().toString();
        });
        return text[0];
    }
    private void captionContains(String text) {
        await(() -> captionText().contains(text),"Live captions never displayed: " + text);
    }
    private void verifyCaptionLayout(boolean fullText) {
        final boolean[] correct={false};
        await(() -> {
            runOnMainSync(() -> {
                TextView view=activity.getWindow().getDecorView().findViewWithTag("captions");
                float expected=20*activity.getResources().getDisplayMetrics().scaledDensity;
                correct[0]=view.getMinLines()>=6 && Math.abs(view.getTextSize()-expected)<0.1
                    && (!fullText || view.getLineCount()>=6);
            });
            return correct[0];
        },"Caption area did not show six lines at the unchanged font size");
    }
    private void verifyCaptionCache(Context context,JSONObject handoff) throws Exception {
        JSONArray captions=handoff.getJSONArray("captions");
        require(captions.length()==handoff.getJSONArray("tracks").length(),"Not all tracks have offline captions");
        for (int i=0;i<captions.length();i++) {
            JSONObject caption=captions.getJSONObject(i);
            File file=new File(new File(context.getFilesDir(),"captions"),caption.getString("sha256")+".vtt");
            require(Transfer.valid(file,caption.getLong("bytes"),caption.getString("sha256")),"Offline caption checksum mismatch");
        }
    }

    private String control(String base,String path) throws Exception {
        java.net.HttpURLConnection connection=(java.net.HttpURLConnection)new URL(base+path).openConnection();
        connection.setConnectTimeout(3000); connection.setReadTimeout(3000);
        try (InputStream input=connection.getInputStream()) { return Integer.toString(input.read()); }
        finally { connection.disconnect(); }
    }

    private void pairingChange(Context context,SharedPreferences prefs) throws Exception {
        String base=arguments.getString("server"), token=arguments.getString("token");
        ConnectivityManager connectivity=context.getSystemService(ConnectivityManager.class);
        await(() -> connectivity.getActiveNetwork()!=null,"Pairing test is waiting for Wi-Fi to reconnect");
        context.getSystemService(android.app.job.JobScheduler.class).cancel(601);
        synchronized (SyncClient.class) {
            prefs.edit().putString("server",base).putString("token",token).commit();
            for (String name:new String[]{"audio","captions"}) {
                File[] files=new File(context.getFilesDir(),name).listFiles();
                if (files!=null) for (File file:files) require(file.delete(),"Cannot reset test download cache");
            }
        }
        String previous=prefs.getString("handoff","");
        require(!new JSONObject(previous).optString("revision").equals("runtime-pairing-change"),"Background job invalidated pairing-change fixture before the test");
        final Exception[] failure={null};
        Thread worker=new Thread(() -> {
            try { SyncClient.sync(context,() -> false); }
            catch (Exception exception) { failure[0]=exception; }
        });
        worker.start();
        try {
            await(() -> {
                try { return control(base,"/_runtime/audio-started").equals("49"); }
                catch (Exception unavailable) { return false; }
            },"Download did not reach the pairing-change barrier");
            synchronized (SyncClient.PAIRING_LOCK) {
                prefs.edit().putString("server","http://127.0.0.1:1").putString("token","changed-runtime-token").commit();
            }
            control(base,"/_runtime/release-audio");
            worker.join(60000);
            require(!worker.isAlive(),"Changed pairing did not finish its original download");
            require(failure[0]!=null && failure[0].getMessage().contains("Pairing settings changed"),"In-flight requests did not retain verified pairing: " + failure[0]);
            require(previous.equals(prefs.getString("handoff","")),"Changed pairing published an old server's handoff");
        } finally {
            control(base,"/_runtime/release-audio");
            worker.interrupt(); worker.join(5000);
            synchronized (SyncClient.PAIRING_LOCK) { prefs.edit().putString("server",base).putString("token",token).commit(); }
        }
    }

    @Override public void onStart() {
        Bundle result=new Bundle();
        try {
            Context context=getTargetContext();
            SharedPreferences prefs=SyncClient.prefs(context);
            String phase=arguments.getString("phase","online");
            if (phase.equals("pairing_change")) {
                pairingChange(context,prefs);
                result.putString("stream","PASS pairing_change: verified endpoint and token retained through concurrent settings edit; handoff not published\n");
                finish(Activity.RESULT_OK,result); return;
            }
            boolean background=phase.equals("background");
            boolean bookSwitch=phase.equals("book_switch");
            boolean coldSeek=phase.equals("cold_seek");
            if (!phase.equals("offline") && !coldSeek) {
                ConnectivityManager connectivity=context.getSystemService(ConnectivityManager.class);
                await(() -> connectivity.getActiveNetwork()!=null,"Online test is waiting for Wi-Fi to reconnect");
                if (!bookSwitch) prefs.edit().putString("server",arguments.getString("server")).putString("token",arguments.getString("token")).commit();
                if (!background && !bookSwitch) SyncClient.sync(context,() -> false);
            }
            if (background) {
                SyncClient.status(context,"Waiting for native background job");
                SyncClient.schedule(context);
                String response;
                try (InputStream in=new ParcelFileDescriptor.AutoCloseInputStream(getUiAutomation().executeShellCommand("cmd jobscheduler run -f dev.kobosync 601"));
                     ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                    byte[] buffer=new byte[1024]; int n;
                    while ((n=in.read(buffer))!=-1) out.write(buffer,0,n);
                    response=out.toString("UTF-8");
                }
                require(response.contains("Running job"),"JobScheduler did not accept the job: " + response);
                await(() -> prefs.getString("handoff","").contains("runtime-background") && prefs.getString("status","").startsWith("Offline audio ready:"),
                    "Native background job did not finish the download");
            }
            JSONObject handoff=new JSONObject(prefs.getString("handoff","{}"));
            String revision=handoff.getString("revision");
            JSONArray tracks=handoff.getJSONArray("tracks");
            for (int i=0;i<tracks.length();i++) {
                JSONObject track=tracks.getJSONObject(i);
                require(Transfer.valid(SyncClient.trackFile(context,track),track.getLong("bytes"),track.getString("sha256")),"Offline track failed validation");
            }
            if (background) {
                verifyCaptionCache(context,handoff);
                require(!revision.equals(prefs.getString("applied_revision","")),"Background download unexpectedly consumed the playback position");
                // Keep the next phase's initial cached book stable until it
                // has actually started, despite onCreate scheduling a job.
                synchronized (SyncClient.class) { prefs.edit().putString("server","").commit(); }
                result.putString("stream","PASS background: revision=" + revision + ", tracks=" + tracks.length() + ", native JobScheduler download verified\n");
                finish(Activity.RESULT_OK,result);
                return;
            }
            if (coldSeek) prefs.edit().putString("applied_revision",revision).putInt("track",0).putLong("position_ms",50000).commit();
            long previous=prefs.getLong("position_ms",0);
            if (phase.equals("online")) {
                // Simulate upgrading an already-playing legacy manifest. A
                // metadata refresh must add captions without rewinding audio.
                JSONObject legacy=new JSONObject(handoff.toString()); legacy.remove("captions");
                synchronized (SyncClient.class) { prefs.edit().putString("handoff",legacy.toString()).putString("server","").commit(); }
            }
            activity=startActivitySync(new Intent(context,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            if (coldSeek) runOnMainSync(() -> context.startForegroundService(new Intent(context,PlaybackService.class).setAction("back")));
            else startPlayer();
            MediaController playing=playingController();
            if (phase.equals("online")) {
                runOnMainSync(() -> serviceBound=context.bindService(new Intent(context,PlaybackService.class),playbackConnection,0));
                require(serviceBound,"Cannot observe actual player position");
                await(() -> localPlayback!=null,"Actual player position binder did not connect");
            }
            long actualStart=playing.getPlaybackState().getPosition();
            long expectedStart=coldSeek ? 20000 : phase.equals("offline") ? previous : (long)(handoff.getJSONObject("position").getDouble("seconds")*1000);
            require(actualStart>=expectedStart-500 && actualStart<expectedStart+2500,
                "Audio began at " + actualStart + "ms; expected " + expectedStart + "ms");
            if (phase.equals("offline")) {
                await(() -> captionText().contains("Second track") || captionText().contains("while offline") || captionText().contains("after a restart"),
                    "Cached captions did not display after an offline process restart");
                getUiAutomation().executeShellCommand("input keyevent 223").close();
                await(() -> !context.getSystemService(PowerManager.class).isInteractive(),"Android screen did not turn off");
            }
            if (phase.equals("online")) {
                captionContains("Sync to download captions");
                prefs.edit().putString("server",arguments.getString("server")).commit();
                SyncClient.sync(context,() -> false);
                require(playing.getPlaybackState().getState()==PlaybackState.STATE_PLAYING,"Caption metadata refresh interrupted playback");
                captionContains("The morning sunlight");
                captionContains("quietly opened");
                captionContains("into the silent garden");
                verifyCaptionLayout(false);
                pausePlayer();
                SystemClock.sleep(350);
                String pausedCaption=captionText();
                SystemClock.sleep(500);
                require(pausedCaption.equals(captionText()),"Paused captions advanced without audio");
                playing.getTransportControls().seekTo(20000);
                captionContains("into the silent garden");
                require(playing.getPlaybackState().getState()==PlaybackState.STATE_PAUSED,"Caption seeking resumed paused audio");
                playing.getTransportControls().seekTo(30000);
                await(() -> Math.abs(playerPosition()-30000)<500,"Did not seek into caption gap");
                SystemClock.sleep(350);
                require(!captionText().isEmpty(),"Caption pane went blank between timed cues");
                releaseBinding();
                playing.getTransportControls().play();
                verifyCaptionCache(context,handoff);
                playing.getTransportControls().seekTo(58000);
                await(() -> prefs.getInt("track",-1)==1 && prefs.getLong("position_ms",0)>1000,"Playback did not cross into second cached track");
                captionContains("while offline playback");
                pausePlayer();
                String beforeSeek=captionText();
                playing.getTransportControls().seekTo(35000);
                await(() -> !captionText().equals(beforeSeek),"Long caption window did not follow a seek");
                verifyCaptionLayout(true);
                playing.getTransportControls().seekTo(5000);
                playing.getTransportControls().play();
            } else if (phase.equals("offline")) {
                verifyCaptionCache(context,handoff);
                require(previous>0,"Missing saved listening position from previous process");
                long sleepingPosition=prefs.getLong("position_ms",0);
                await(() -> prefs.getInt("track",-1)==1 && prefs.getLong("position_ms",0)>Math.max(previous,sleepingPosition)+1000,"Offline playback did not advance with the screen off");
                require(!context.getSystemService(PowerManager.class).isInteractive(),"Screen woke during offline playback check");
            } else if (coldSeek) {
                captionContains("into the silent garden");
                verifyCaptionCache(context,handoff);
            } else if (bookSwitch) {
                require(playing.getMetadata().getString(MediaMetadata.METADATA_KEY_TITLE).equals(handoff.getString("title")),"Expected cached book was not playing before the download");
                verifyCaptionCache(context,handoff);
                JSONObject caption=handoff.getJSONArray("captions").getJSONObject(0);
                CaptionTimeline current=CaptionTimeline.load(new File(new File(context.getFilesDir(),"captions"),caption.getString("sha256")+".vtt"));
                prefs.edit().putString("server",arguments.getString("server")).commit();
                SyncClient.sync(context,() -> false);
                JSONObject downloaded=new JSONObject(prefs.getString("handoff","{}"));
                require(!downloaded.getString("book_id").equals(handoff.getString("book_id")),"Different-book fixture did not download");
                require(playing.getMetadata().getString(MediaMetadata.METADATA_KEY_TITLE).equals(handoff.getString("title")),"Downloading changed the playing book");
                await(() -> {
                    PlaybackState state=playing.getPlaybackState();
                    long position=state.getPosition()+(long)((SystemClock.elapsedRealtime()-state.getLastPositionUpdateTime())*state.getPlaybackSpeed());
                    String expected=current.windowAt(position);
                    return !expected.isEmpty() && expected.equals(captionText());
                },"Captions changed to the downloaded book instead of following the playing book");
                verifyCaptionCache(context,downloaded);
            } else {
                captionContains("into the silent garden");
                await(() -> revision.equals(prefs.getString("applied_revision","")) && prefs.getInt("track",-1)==0 && prefs.getLong("position_ms",0)>21000,"New handoff did not apply its target timestamp");
            }
            pausePlayer();
            long saved=prefs.getLong("position_ms",0);
            require(revision.equals(prefs.getString("applied_revision","")),"Wrong handoff revision consumed");
            if (!phase.equals("offline") && !coldSeek) {
                SyncClient.sync(context,() -> false);
                require(saved==prefs.getLong("position_ms",0),"Repeated sync reset listening progress");
            }
            result.putString("stream","PASS " + phase + ": revision=" + revision + ", start_ms=" + actualStart + ", target_ms=" + expectedStart + ", track=" + prefs.getInt("track",-1) + ", saved_ms=" + saved + ", captions verified\n");
            releaseBinding();
            runOnMainSync(() -> { context.stopService(new Intent(context,PlaybackService.class)); if (activity!=null) activity.finish(); });
            finish(Activity.RESULT_OK,result);
        } catch (Throwable e) {
            releaseBinding();
            result.putString("stream","FAIL: " + android.util.Log.getStackTraceString(e));
            finish(Activity.RESULT_CANCELED,result);
        }
    }
}
