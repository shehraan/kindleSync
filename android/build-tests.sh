#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
task_sdk=${ANDROID_SDK_ROOT:-"$task_root/.tools/android-sdk"}
task_jdk=${JAVA_HOME:-"$task_root/.tools/jdk"}
task_build="$task_root/android/build"
task_tools="$task_sdk/build-tools/35.0.0"
task_platform="$task_sdk/platforms/android-35/android.jar"
mkdir -p "$task_build/instrumentation-classes" "$task_build/instrumentation-dex"
"$task_jdk/bin/javac" -source 8 -target 8 -Xlint:-options -classpath "$task_platform:$task_build/classes" -d "$task_build/instrumentation-classes" "$task_root/android/tests/PlaybackSmoke.java"
"$task_jdk/bin/jar" cf "$task_build/target-classes.jar" -C "$task_build/classes" .
"$task_jdk/bin/java" -cp "$task_tools/lib/d8.jar" com.android.tools.r8.D8 --lib "$task_platform" --classpath "$task_build/target-classes.jar" --min-api 29 --output "$task_build/instrumentation-dex" "$task_build"/instrumentation-classes/dev/kobosync/*.class
"$task_tools/aapt2" link --manifest "$task_root/android/tests/AndroidManifest.xml" -I "$task_platform" --min-sdk-version 29 --target-sdk-version 35 -o "$task_build/test-unsigned.apk"
python3 - "$task_build" <<'PY'
import pathlib,sys,zipfile
p=pathlib.Path(sys.argv[1])
with zipfile.ZipFile(p/'test-unsigned.apk','a') as z:
    for f in (p/'instrumentation-dex').glob('*.dex'): z.write(f,f.name)
PY
"$task_tools/zipalign" -f 4 "$task_build/test-unsigned.apk" "$task_build/test-aligned.apk"
"$task_jdk/bin/java" -jar "$task_tools/lib/apksigner.jar" sign --ks "$task_build/local-debug.jks" --ks-pass pass:android --out "$task_build/kobosync-test.apk" "$task_build/test-aligned.apk"
"$task_jdk/bin/java" -jar "$task_tools/lib/apksigner.jar" verify "$task_build/kobosync-test.apk"
