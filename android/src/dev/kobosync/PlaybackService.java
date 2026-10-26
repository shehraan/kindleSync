package dev.kobosync;

import android.app.*;
import android.content.*;
import android.media.*;
import android.media.session.*;
import android.os.*;
import org.json.*;
import java.io.*;

public final class PlaybackService extends Service {
    private MediaPlayer player;
    private MediaSession session;
    private AudioManager audio;
    private AudioFocusRequest focus;
    private JSONObject handoff;
    private int track;
    private String revision="";
    private boolean prepared=false;
    private final Handler timer=new Handler();
    private final Runnable checkpoint=new Runnable() { public void run() { save(); timer.postDelayed(this,5000); } };
    /** Read-only live position; queried on the same main thread as MediaPlayer. */
    public static final class Snapshot {
        public final JSONObject manifest;
        public final int track;
        public final long positionMs;
        Snapshot(JSONObject manifest,int track,long positionMs) { this.manifest=manifest; this.track=track; this.positionMs=positionMs; }
    }
    public final class LocalBinder extends Binder {
        public Snapshot snapshot() {
            if (handoff==null || !prepared || player==null) return null;
            try { return new Snapshot(handoff,track,player.getCurrentPosition()); }
            catch (IllegalStateException e) { return null; }
        }
    }
    private final LocalBinder binder=new LocalBinder();

    @Override public void onCreate() {
        super.onCreate();
        getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel("playback","Audiobook playback",NotificationManager.IMPORTANCE_LOW));
        audio=getSystemService(AudioManager.class);
        focus=new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setOnAudioFocusChangeListener(change -> { if (change != AudioManager.AUDIOFOCUS_GAIN && player != null && prepared && player.isPlaying()) { player.pause(); save(); notifyState(); } }).build();
        session=new MediaSession(this,"KoboSync");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { play(); }
            @Override public void onPause() { pause(); }
            @Override public void onSeekTo(long pos) { if (prepared) { player.seekTo(pos,MediaPlayer.SEEK_CLOSEST); save(); } }
            @Override public void onSkipToNext() { seek(30000); }
            @Override public void onSkipToPrevious() { seek(-30000); }
        });
        session.setActive(true); timer.post(checkpoint);
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        startForeground(1,notification());
        String action=intent==null ? "resume" : intent.getAction();
        try {
            if ("resume".equals(action) || handoff==null) {
                JSONObject next=new JSONObject(SyncClient.prefs(this).getString("handoff","{}"));
                String nextRevision=next.getString("revision");
                if (nextRevision.equals(revision) && player!=null) play();
                else {
                    save(); handoff=next; revision=nextRevision;
                    boolean applied=revision.equals(SyncClient.prefs(this).getString("applied_revision",""));
                    track=applied ? SyncClient.prefs(this).getInt("track",0) : handoff.getJSONObject("position").getInt("track");
                    long ms=applied ? SyncClient.prefs(this).getLong("position_ms",0) : (long)(handoff.getJSONObject("position").getDouble("seconds")*1000);
                    if ("back".equals(action)) ms=Math.max(0,ms-30000);
                    else if ("forward".equals(action)) ms+=30000;
                    openTrack(ms);
                }
            } else if ("toggle".equals(action)) { if (prepared && player.isPlaying()) pause(); else play(); }
            else if ("pause".equals(action)) pause();
            else if ("play".equals(action)) play();
            else if ("back".equals(action)) seek(-30000);
            else if ("forward".equals(action)) seek(30000);
        } catch (Exception e) { SyncClient.status(this,"Playback: " + e.getMessage()); stopSelf(); }
        return START_NOT_STICKY;
    }
    private void openTrack(long ms) throws Exception {
        if (player!=null) player.release(); prepared=false;
        JSONArray tracks=handoff.getJSONArray("tracks");
        if (track<0 || track>=tracks.length()) throw new IOException("Invalid saved audio track");
        JSONObject item=tracks.getJSONObject(track);
        File file=SyncClient.trackFile(this,item);
        if (!file.isFile() || file.length()!=item.getLong("bytes")) throw new IOException("Audio is not fully downloaded yet");
        player=new MediaPlayer();
        player.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
        player.setWakeMode(this,PowerManager.PARTIAL_WAKE_LOCK);
        player.setDataSource(file.getAbsolutePath());
        player.setOnPreparedListener(p -> {
            prepared=true;
            SyncClient.prefs(this).edit().putString("applied_revision",revision).putInt("track",track).putLong("position_ms",ms).commit();
            p.setOnSeekCompleteListener(mp -> { mp.setOnSeekCompleteListener(null); play(); });
            p.seekTo(ms,MediaPlayer.SEEK_CLOSEST);
        });
        player.setOnCompletionListener(p -> {
            save();
            try {
                if (track+1 < handoff.getJSONArray("tracks").length()) { track++; openTrack(0); }
                else { SyncClient.status(this,"Finished " + handoff.optString("title")); stopSelf(); }
            } catch (Exception e) { SyncClient.status(this,e.getMessage()); stopSelf(); }
        });
        player.setOnErrorListener((p,what,extra) -> { SyncClient.status(this,"Audio decoder error " + what + "/" + extra); stopSelf(); return true; });
        player.prepareAsync();
    }
    private void play() {
        if (player!=null && prepared && audio.requestAudioFocus(focus)==AudioManager.AUDIOFOCUS_REQUEST_GRANTED) { player.start(); notifyState(); }
    }
    private void pause() { if (player!=null && prepared) { player.pause(); save(); notifyState(); } }
    private void seek(long delta) {
        if (player!=null && prepared) player.seekTo(Math.max(0,Math.min(player.getDuration(),player.getCurrentPosition()+delta)),MediaPlayer.SEEK_CLOSEST);
    }
    private void save() {
        if (prepared && player!=null) SyncClient.prefs(this).edit().putString("applied_revision",revision).putInt("track",track).putLong("position_ms",player.getCurrentPosition()).commit();
    }
    private PendingIntent command(String action,int id) {
        return PendingIntent.getService(this,id,new Intent(this,PlaybackService.class).setAction(action),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
    }
    private Notification notification() {
        boolean playing=player!=null && prepared && player.isPlaying();
        return new Notification.Builder(this,"playback").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(handoff==null ? "KoboSync" : handoff.optString("title","KoboSync"))
            .setContentText("Offline audiobook · track " + (track+1))
            .setContentIntent(PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE))
            .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_rew,"Back 30 seconds",command("back",2)).build())
            .addAction(new Notification.Action.Builder(playing ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play,playing ? "Pause" : "Play",command(playing ? "pause" : "play",3)).build())
            .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_ff,"Forward 30 seconds",command("forward",4)).build())
            .setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()).setShowActionsInCompactView(0,1,2))
            .setOngoing(playing).build();
    }
    private void notifyState() {
        if (!prepared) return;
        boolean playing=player.isPlaying();
        session.setPlaybackState(new PlaybackState.Builder().setActions(PlaybackState.ACTION_PLAY|PlaybackState.ACTION_PAUSE|PlaybackState.ACTION_PLAY_PAUSE|PlaybackState.ACTION_SEEK_TO|PlaybackState.ACTION_SKIP_TO_NEXT|PlaybackState.ACTION_SKIP_TO_PREVIOUS)
            .setState(playing ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED,player.getCurrentPosition(),playing ? 1f : 0f).build());
        session.setMetadata(new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE,handoff.optString("title")).putLong(MediaMetadata.METADATA_KEY_DURATION,player.getDuration()).build());
        getSystemService(NotificationManager.class).notify(1,notification());
    }
    @Override public void onDestroy() { save(); timer.removeCallbacks(checkpoint); if (player!=null) player.release(); if (session!=null) session.release(); if (audio!=null) audio.abandonAudioFocusRequest(focus); stopForeground(STOP_FOREGROUND_REMOVE); super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return binder; }
}
