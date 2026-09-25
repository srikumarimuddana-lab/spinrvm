package expo.modules.rideoffertone

import android.annotation.TargetApi
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * Plays the bundled ride-offer tone (res/raw/ride_offer, copied by
 * plugins/withRideOfferSound) as a navigation prompt, so Android Auto routes it
 * to the car speakers and ducks the driver's music instead of pausing it.
 *
 * Why native: expo-audio exposes no AudioAttributes usage and its
 * mixWithOthers mode takes no audio focus, so it can neither duck nor be
 * classified as navigation guidance, and it would show up in Android Auto as a
 * media source. See lib/androidAuto/carOfferRing.ts for the JS ring owner.
 *
 * Rules:
 * - never rings over a call (AudioManager mode != MODE_NORMAL);
 * - waits up to 3 s for an in-flight navigation prompt or voice call stream
 *   before the first play, so it never cuts off a turn instruction;
 * - loops with a gap until maxMs, then stops on its own;
 * - pauses on a transient focus loss (a nav prompt) and resumes on regain.
 *
 * All state lives on the main looper; the exported functions only post to it.
 */
class RideOfferToneModule : Module() {
  private val handler = Handler(Looper.getMainLooper())

  // Token for every delayed callback this module schedules, so stop() can
  // remove exactly those without also removing a start()/stop() post that is
  // queued behind it.
  private val timerToken = Any()

  private var player: MediaPlayer? = null
  private var focusRequest: AudioFocusRequest? = null
  private var pendingStart: Promise? = null
  private var generation = 0
  private var startedAt = 0L
  private var maxMs = 0
  private var gapMs = 0
  private var pausedForFocus = false

  private val context: Context?
    get() = appContext.reactContext?.applicationContext

  override fun definition() = ModuleDefinition {
    Name("RideOfferTone")

    Function("isSupported") {
      isSupported()
    }

    AsyncFunction("start") { requestedMaxMs: Int, requestedGapMs: Int, promise: Promise ->
      handler.post { startOnMain(requestedMaxMs, requestedGapMs, promise) }
      Unit
    }

    Function("stop") {
      handler.post { stopOnMain() }
      Unit
    }

    OnDestroy {
      handler.post { stopOnMain() }
    }
  }

  private fun rawId(ctx: Context): Int =
    ctx.resources.getIdentifier("ride_offer", "raw", ctx.packageName)

  private fun isSupported(): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return false
    val ctx = context ?: return false
    return try {
      rawId(ctx) != 0
    } catch (e: Exception) {
      Log.e(TAG, "isSupported: resource lookup failed", e)
      false
    }
  }

  private fun audioManager(ctx: Context?): AudioManager? =
    ctx?.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

  private fun schedule(delayMs: Long, block: () -> Unit) {
    handler.postAtTime(Runnable { block() }, timerToken, SystemClock.uptimeMillis() + delayMs)
  }

  private fun startOnMain(requestedMaxMs: Int, requestedGapMs: Int, promise: Promise) {
    stopOnMain() // a new offer replaces the old tone
    val ctx = context
    val am = audioManager(ctx)
    if (ctx == null || am == null || !isSupported()) {
      promise.resolve("unsupported")
      return
    }
    if (am.mode != AudioManager.MODE_NORMAL) {
      Log.i(TAG, "start: audio mode ${am.mode}, not ringing over a call")
      promise.resolve("blocked_call")
      return
    }
    maxMs = requestedMaxMs.coerceIn(MIN_MAX_MS, MAX_MAX_MS)
    gapMs = requestedGapMs.coerceIn(0, MAX_GAP_MS)
    startedAt = SystemClock.elapsedRealtime()
    val gen = ++generation
    pendingStart = promise
    // Hard stop for the whole ring, including any wait for a nav prompt.
    schedule(maxMs.toLong()) { if (gen == generation) stopOnMain() }
    waitThenPlay(gen, ctx, am, 0)
  }

  // Only reached after isSupported() (API 26+) passed in startOnMain.
  @TargetApi(Build.VERSION_CODES.O)
  private fun isPromptOrCallPlaying(am: AudioManager): Boolean =
    try {
      am.activePlaybackConfigurations.any {
        val usage = it.audioAttributes.usage
        usage == AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE ||
          usage == AudioAttributes.USAGE_VOICE_COMMUNICATION
      }
    } catch (e: Exception) {
      Log.w(TAG, "activePlaybackConfigurations unavailable", e)
      false
    }

  private fun waitThenPlay(gen: Int, ctx: Context, am: AudioManager, waitedMs: Int) {
    if (gen != generation) return
    if (am.mode != AudioManager.MODE_NORMAL) {
      resolvePending("blocked_call")
      stopOnMain()
      return
    }
    if (waitedMs < PROMPT_WAIT_MS && isPromptOrCallPlaying(am)) {
      schedule(PROMPT_POLL_MS.toLong()) { waitThenPlay(gen, ctx, am, waitedMs + PROMPT_POLL_MS) }
      return
    }
    playNow(gen, ctx, am)
  }

  @TargetApi(Build.VERSION_CODES.O)
  private fun playNow(gen: Int, ctx: Context, am: AudioManager) {
    try {
      val attrs = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
        .build()
      val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(attrs)
        .setWillPauseWhenDucked(true)
        .setOnAudioFocusChangeListener({ change -> onFocusChange(gen, change) }, handler)
        .build()
      focusRequest = request
      val granted = am.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
      if (!granted) {
        // e.g. Android 15 refusing focus to a background app. Still ring: the
        // driver missing an offer is worse than music not ducking.
        Log.w(TAG, "audio focus not granted, playing unfocused")
      }
      val mp = MediaPlayer.create(ctx, rawId(ctx), attrs, am.generateAudioSessionId())
      if (mp == null) {
        Log.e(TAG, "MediaPlayer.create returned null")
        resolvePending("error")
        stopOnMain()
        return
      }
      mp.setOnCompletionListener { onCompletion(gen) }
      mp.setOnErrorListener { _, what, extra ->
        Log.e(TAG, "MediaPlayer error what=$what extra=$extra")
        if (gen == generation) stopOnMain()
        true
      }
      player = mp
      mp.start()
      resolvePending(if (granted) "playing" else "playing_unfocused")
    } catch (e: Exception) {
      Log.e(TAG, "start failed", e)
      resolvePending("error")
      stopOnMain()
    }
  }

  private fun onCompletion(gen: Int) {
    if (gen != generation) return
    val elapsed = SystemClock.elapsedRealtime() - startedAt
    if (elapsed + gapMs >= maxMs) {
      stopOnMain()
      return
    }
    schedule(gapMs.toLong()) {
      if (gen != generation || pausedForFocus) return@schedule
      try {
        player?.seekTo(0)
        player?.start()
      } catch (e: Exception) {
        Log.e(TAG, "replay failed", e)
        stopOnMain()
      }
    }
  }

  private fun onFocusChange(gen: Int, change: Int) {
    if (gen != generation) return
    val mp = player ?: return
    try {
      when (change) {
        AudioManager.AUDIOFOCUS_LOSS_TRANSIENT,
        AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> {
          pausedForFocus = true
          if (mp.isPlaying) mp.pause()
        }
        AudioManager.AUDIOFOCUS_GAIN -> {
          if (pausedForFocus) {
            pausedForFocus = false
            mp.start()
          }
        }
        AudioManager.AUDIOFOCUS_LOSS -> stopOnMain()
      }
    } catch (e: Exception) {
      Log.e(TAG, "focus change $change handling failed", e)
      stopOnMain()
    }
  }

  private fun resolvePending(result: String) {
    val p = pendingStart ?: return
    pendingStart = null
    p.resolve(result)
  }

  /** Idempotent. Main thread only. focusRequest is only ever set on API 26+. */
  @TargetApi(Build.VERSION_CODES.O)
  private fun stopOnMain() {
    generation++
    handler.removeCallbacksAndMessages(timerToken)
    // A start still waiting out a nav prompt was superseded, not failed.
    resolvePending("cancelled")
    player?.let {
      try {
        if (it.isPlaying) it.stop()
      } catch (e: Exception) {
        Log.w(TAG, "stop failed", e)
      }
      it.release()
    }
    player = null
    focusRequest?.let { req ->
      try {
        audioManager(context)?.abandonAudioFocusRequest(req)
      } catch (e: Exception) {
        Log.w(TAG, "abandon focus failed", e)
      }
    }
    focusRequest = null
    pausedForFocus = false
  }

  companion object {
    private const val TAG = "RideOfferTone"
    private const val MIN_MAX_MS = 1_000
    private const val MAX_MAX_MS = 60_000
    private const val MAX_GAP_MS = 10_000
    private const val PROMPT_WAIT_MS = 3_000
    private const val PROMPT_POLL_MS = 250
  }
}
