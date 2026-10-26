package dev.kobosync;
import android.app.job.*;
import java.util.concurrent.atomic.AtomicBoolean;

public final class SyncJob extends JobService {
    private AtomicBoolean cancelled;
    @Override public boolean onStartJob(JobParameters params) {
        AtomicBoolean thisRun = new AtomicBoolean(false); cancelled = thisRun;
        new Thread(() -> {
            boolean retry = false;
            try { SyncClient.sync(this,thisRun::get); }
            catch (Exception e) { SyncClient.status(this,"Sync pending: " + e.getMessage()); retry = true; }
            if (!thisRun.get()) jobFinished(params,retry);
        },"KoboSync-download").start();
        return true;
    }
    @Override public boolean onStopJob(JobParameters params) { if (cancelled != null) cancelled.set(true); return true; }
}
