@echo off
set ANDROID_HOME=C:\android-sdk
set ANDROID_SDK_ROOT=C:\android-sdk
set JAVA_HOME=d:\Spinr\app\java-17\jdk-17.0.11+9
set PATH=%JAVA_HOME%\bin;%PATH%
cd /d d:\Spinr\app\spinr\rider-app\android
call gradlew.bat assembleDebug
