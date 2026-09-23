package com.echoink.mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import java.util.concurrent.Executors
import kotlin.math.abs

/**
 * Shows the floating mic over other apps. Tap it to record, tap again to send the
 * recording to the PC; the text is typed into the focused field by TextInsertService.
 *
 * It runs as a microphone foreground service started from the app's screen, which is
 * what lets it record while other apps are in front.
 */
class FloatingMicService : Service() {
    private lateinit var settings: Settings
    private lateinit var windowManager: WindowManager
    private lateinit var params: WindowManager.LayoutParams
    private var button: MicButton? = null
    private val recorder = AudioRecorder()
    private val network = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val autoStop = Runnable { if (recorder.isRecording) finishRecording() }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        windowManager = getSystemService(WindowManager::class.java)
        try {
            startInForeground()
            showButton()
            running = true
        } catch (e: Exception) {
            toast("Floating mic could not start: ${e.message}")
            stopSelf()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) stopSelf()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        main.removeCallbacks(autoStop)
        if (recorder.isRecording) recorder.stop()
        button?.let { windowManager.removeView(it) }
        button = null
        network.shutdown()
        super.onDestroy()
    }

    private fun startInForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Floating mic", NotificationManager.IMPORTANCE_LOW)
        )
        val stop = PendingIntent.getService(
            this, 0, Intent(this, FloatingMicService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("EchoInk floating mic is on")
            .setContentText("Tap the mic to dictate, tap again to insert the text.")
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .setOngoing(true)
            .build()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, type)
    }

    private fun showButton() {
        val metrics = resources.displayMetrics
        val size = (68 * metrics.density).toInt()
        params = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // Not focusable: tapping the mic must not take focus from the text field.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = if (settings.bubbleX >= 0) settings.bubbleX else metrics.widthPixels - size
            y = if (settings.bubbleY >= 0) settings.bubbleY else metrics.heightPixels / 2
        }
        val mic = MicButton(this)
        mic.setOnClickListener { onTap(it) }
        mic.setOnTouchListener(DragOrTap())
        windowManager.addView(mic, params)
        button = mic
    }

    /** Drags the bubble around; a touch that doesn't move counts as a tap. */
    private inner class DragOrTap : View.OnTouchListener {
        private val slop = ViewConfiguration.get(this@FloatingMicService).scaledTouchSlop
        private var downX = 0f
        private var downY = 0f
        private var startX = 0
        private var startY = 0
        private var dragging = false

        override fun onTouch(view: View, event: MotionEvent): Boolean {
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    startX = params.x
                    startY = params.y
                    dragging = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (!dragging && (abs(dx) > slop || abs(dy) > slop)) dragging = true
                    if (dragging) {
                        params.x = startX + dx.toInt()
                        params.y = startY + dy.toInt()
                        windowManager.updateViewLayout(view, params)
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (dragging) {
                        settings.bubbleX = params.x
                        settings.bubbleY = params.y
                    } else {
                        view.performClick()
                    }
                }
            }
            return true
        }
    }

    private fun onTap(view: View) {
        view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
        when (button?.state) {
            MicButton.State.IDLE -> startRecording()
            MicButton.State.RECORDING -> finishRecording()
            else -> Unit // still transcribing the previous recording
        }
    }

    private fun startRecording() {
        if (!settings.isPaired) {
            toast("Open EchoInk and pair it with your PC first")
            return
        }
        try {
            recorder.start()
        } catch (e: Exception) {
            toast("Cannot record: ${e.message}")
            return
        }
        button?.state = MicButton.State.RECORDING
        main.postDelayed(autoStop, MAX_RECORDING_MS)
    }

    private fun finishRecording() {
        main.removeCallbacks(autoStop)
        val wav = recorder.stop()
        if (wav.size < 44 + AudioRecorder.RATE / 2) { // under 0.25 s: an accidental tap
            button?.state = MicButton.State.IDLE
            return
        }
        button?.state = MicButton.State.SENDING
        network.execute {
            val result = runCatching { EchoInkApi.transcribe(settings, wav) }
            main.post {
                button?.state = MicButton.State.IDLE
                result.onSuccess { deliver(it.text) }
                    .onFailure { toast(it.message ?: "Transcription failed") }
            }
        }
    }

    private fun deliver(text: String) {
        if (text.isBlank()) {
            toast("No speech recognized")
            return
        }
        if (TextInsertService.insert(text)) return
        getSystemService(ClipboardManager::class.java)
            .setPrimaryClip(ClipData.newPlainText("EchoInk", text))
        toast(
            if (TextInsertService.isEnabled) "No text field is focused; the text is on the clipboard"
            else "Copied. Turn on EchoInk dictation in Accessibility to type it automatically"
        )
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    companion object {
        const val ACTION_STOP = "com.echoink.mobile.STOP"
        private const val CHANNEL = "floating_mic"
        private const val NOTIFICATION_ID = 1
        private const val MAX_RECORDING_MS = 60_000L

        @Volatile
        var running = false
            private set
    }
}
