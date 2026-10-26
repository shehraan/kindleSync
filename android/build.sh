#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
task_sdk=${ANDROID_SDK_ROOT:-"$task_root/.tools/android-sdk"}
task_jdk=${JAVA_HOME:-"$task_root/.tools/jdk"}
task_build="$task_root/android/build"
task_tools="$task_sdk/build-tools/35.0.0"
task_platform="$task_sdk/platforms/android-35/android.jar"
mkdir -p "$task_build/classes" "$task_build/dex" "$task_build/test-classes"
if [ ! -f "$task_platform" ] || [ ! -x "$task_tools/aapt2" ]; then
    echo 'Install Android SDK platforms;android-35 and build-tools;35.0.0. Set ANDROID_SDK_ROOT and JAVA_HOME if needed.' >&2
    exit 1
fi
"$task_jdk/bin/javac" -d "$task_build/test-classes" "$task_root/android/src/dev/kobosync/Transfer.java" "$task_root/android/tests/TransferTest.java" "$task_root/android/src/dev/kobosync/CaptionTimeline.java" "$task_root/android/tests/CaptionTimelineTest.java" "$task_root/android/src/dev/kobosync/ServerDiscovery.java" "$task_root/android/tests/ServerDiscoveryTest.java"
"$task_jdk/bin/java" -cp "$task_build/test-classes" dev.kobosync.TransferTest
"$task_jdk/bin/java" -cp "$task_build/test-classes" dev.kobosync.CaptionTimelineTest
"$task_jdk/bin/java" -cp "$task_build/test-classes" dev.kobosync.ServerDiscoveryTest
"$task_jdk/bin/javac" -source 8 -target 8 -Xlint:-options -classpath "$task_platform" -d "$task_build/classes" "$task_root"/android/src/dev/kobosync/*.java
"$task_tools/aapt2" compile --dir "$task_root/android/res" -o "$task_build/resources.zip"
"$task_tools/aapt2" link --manifest "$task_root/android/AndroidManifest.xml" -I "$task_platform" --min-sdk-version 26 --target-sdk-version 35 -o "$task_build/unsigned.apk" "$task_build/resources.zip"
"$task_jdk/bin/java" -cp "$task_tools/lib/d8.jar" com.android.tools.r8.D8 --lib "$task_platform" --min-api 26 --output "$task_build/dex" "$task_build"/classes/dev/kobosync/*.class
python3 - "$task_build" <<'PY'
import sys,zipfile,pathlib
p=pathlib.Path(sys.argv[1])
with zipfile.ZipFile(p/'unsigned.apk','a') as z:
    for f in (p/'dex').glob('*.dex'): z.write(f,f.name)
PY
"$task_tools/zipalign" -f 4 "$task_build/unsigned.apk" "$task_build/aligned.apk"
if [ ! -f "$task_build/local-debug.jks" ]; then
    "$task_jdk/bin/keytool" -genkeypair -noprompt -keystore "$task_build/local-debug.jks" -storepass android -keypass android -alias kobosync -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=KoboSync Local Development"
fi
"$task_jdk/bin/java" -jar "$task_tools/lib/apksigner.jar" sign --ks "$task_build/local-debug.jks" --ks-pass pass:android --out "$task_build/kobosync.apk" "$task_build/aligned.apk"
"$task_jdk/bin/java" -jar "$task_tools/lib/apksigner.jar" verify --verbose "$task_build/kobosync.apk"
echo "APK: $task_build/kobosync.apk"
