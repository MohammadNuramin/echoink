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
import kotlin.math.hypot

/**
 * Shows the floating mic over other apps. Tap it to record, tap again to send the
 * recording to the PC; the text is typed into the focused field by TextInsertService.
 *
 * Recordings the PC can't receive (EchoInk closed, PC asleep, no network) are kept on
 * the phone and sent once the PC answers again; their text goes to the clipboard.
 *
 * It runs as a microphone foreground service started from the app's screen, which is
 * what lets it record while other apps are in front.
 */
class FloatingMicService : Service() {
    private lateinit var settings: Settings
    private lateinit var pending: PendingRecordings
    private lateinit var windowManager: WindowManager
    private lateinit var params: WindowManager.LayoutParams
    private var button: MicButton? = null
    private var closeTarget: CloseTarget? = null
    private var sendingPending = false
    private val recorder = AudioRecorder()
    private val network = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val autoStop = Runnable { if (recorder.isRecording) finishRecording() }
    private val recheck = Runnable { checkPc(warnIfOffline = false) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        pending = PendingRecordings(this)
        windowManager = getSystemService(WindowManager::class.java)
        try {
            startInForeground()
            showButton()
            running = true
            checkPc(warnIfOffline = false)
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
        main.removeCallbacks(recheck)
        if (recorder.isRecording) recorder.stop()
        hideCloseTarget()
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
        manager.createNotificationChannel(
            NotificationChannel(RESULTS_CHANNEL, "Saved recordings", NotificationManager.IMPORTANCE_DEFAULT)
        )
        val stop = PendingIntent.getService(
            this, 0, Intent(this, FloatingMicService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_mic_line)
            .setContentTitle("EchoInk floating mic is on")
            .setContentText("Tap the mic to dictate. Drag it onto the X at the bottom to close it.")
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
        mic.pendingCount = pending.count
        mic.setOnClickListener { onTap(it) }
        mic.setOnTouchListener(DragOrTap())
        windowManager.addView(mic, params)
        button = mic
    }

    /**
     * Asks the PC whether it is there. While it isn't, the bubble is grey and this repeats
     * every few seconds; once it answers, waiting recordings are sent.
     */
    private fun checkPc(warnIfOffline: Boolean) {
        main.removeCallbacks(recheck)
        if (!running) return // a reply can arrive after the bubble was closed
        network.execute {
            val error = runCatching { EchoInkApi.check(settings) }.exceptionOrNull()
            main.post {
                if (error is EchoInkApi.UnreachableException) {
                    showOffline()
                    if (warnIfOffline) {
                        toast("Your PC is offline. Keep talking: the recording will be sent when it's back.")
                    }
                } else {
                    button?.offline = false
                    if (error != null) toast(error.message ?: "The PC refused the connection")
                    else if (pending.count > 0) sendPending()
                }
            }
        }
    }

    private fun showOffline() {
        if (!running) return
        button?.offline = true
        button?.pendingCount = pending.count
        main.postDelayed(recheck, RECHECK_MS)
    }

    /** Sends the recordings saved while the PC was offline, oldest first. */
    private fun sendPending() {
        if (!running || sendingPending) return
        sendingPending = true
        network.execute {
            val texts = mutableListOf<String>()
            var offline = false
            var refused: String? = null
            while (true) {
                val file = pending.oldest() ?: break
                val result = runCatching { EchoInkApi.transcribe(settings, file.readBytes()) }
                val error = result.exceptionOrNull()
                if (error is EchoInkApi.UnreachableException) {
                    offline = true
                    break
                }
                if (error is EchoInkApi.RejectedException && (error.status == 401 || error.status >= 500)) {
                    refused = error.message // keep the recording and try again later
                    break
                }
                file.delete() // sent, or the PC can't use it (e.g. unreadable audio)
                result.getOrNull()?.text?.takeIf { it.isNotBlank() }?.let { texts += it }
            }
            main.post {
                sendingPending = false
                button?.pendingCount = pending.count
                if (texts.isNotEmpty()) deliverSaved(texts)
                if (offline) showOffline()
                refused?.let { toast(it) }
            }
        }
    }

    /** Text from saved recordings goes to the clipboard, since you may be somewhere else by now. */
    private fun deliverSaved(texts: List<String>) {
        val text = texts.joinToString("\n")
        getSystemService(ClipboardManager::class.java)
            .setPrimaryClip(ClipData.newPlainText("EchoInk", text))
        val title = if (texts.size == 1) "Saved recording transcribed" else "${texts.size} saved recordings transcribed"
        val notification = NotificationCompat.Builder(this, RESULTS_CHANNEL)
            .setSmallIcon(R.drawable.ic_mic_line)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText("$text\n\n(Copied to the clipboard)"))
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(RESULTS_NOTIFICATION_ID, notification)
        toast("Your PC is back. The saved text is on the clipboard.")
    }

    /** The ✕ drop target at the bottom of the screen, shown while the bubble is dragged. */
    private fun showCloseTarget() {
        if (closeTarget != null) return
        val density = resources.displayMetrics.density
        val size = (72 * density).toInt()
        val target = CloseTarget(this)
        windowManager.addView(target, WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = (56 * density).toInt()
        })
        closeTarget = target
    }

    private fun hideCloseTarget() {
        closeTarget?.let { windowManager.removeView(it) }
        closeTarget = null
    }

    private fun isOverCloseTarget(rawX: Float, rawY: Float): Boolean {
        val target = closeTarget ?: return false
        if (target.width == 0) return false
        val location = IntArray(2)
        target.getLocationOnScreen(location)
        val cx = location[0] + target.width / 2f
        val cy = location[1] + target.height / 2f
        return hypot(rawX - cx, rawY - cy) < target.width
    }

    /** Drags the bubble around (drop it on the ✕ to close); a touch that doesn't move is a tap. */
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
                    if (!dragging && (abs(dx) > slop || abs(dy) > slop)) {
                        dragging = true
                        showCloseTarget()
                    }
                    if (dragging) {
                        params.x = startX + dx.toInt()
                        params.y = startY + dy.toInt()
                        windowManager.updateViewLayout(view, params)
                        val near = isOverCloseTarget(event.rawX, event.rawY)
                        if (near && closeTarget?.near == false) {
                            view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                        }
                        closeTarget?.near = near
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (!dragging) {
                        view.performClick()
                    } else if (closeTarget?.near == true) {
                        stopSelf()
                    } else {
                        settings.bubbleX = params.x
                        settings.bubbleY = params.y
                    }
                    hideCloseTarget()
                }
                MotionEvent.ACTION_CANCEL -> hideCloseTarget()
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
        checkPc(warnIfOffline = true) // warns while you talk, instead of after
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
            val offline = result.exceptionOrNull() is EchoInkApi.UnreachableException
            if (offline) pending.add(wav)
            main.post {
                button?.state = MicButton.State.IDLE
                when {
                    offline -> {
                        showOffline()
                        toast("Your PC is offline. The recording is saved and will be sent when it's back.")
                    }
                    result.isSuccess -> {
                        button?.offline = false
                        deliver(result.getOrThrow().text)
                        if (pending.count > 0) sendPending()
                    }
                    else -> toast(result.exceptionOrNull()?.message ?: "Transcription failed")
                }
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
        private const val RESULTS_CHANNEL = "saved_recordings"
        private const val NOTIFICATION_ID = 1
        private const val RESULTS_NOTIFICATION_ID = 2
        private const val MAX_RECORDING_MS = 60_000L
        private const val RECHECK_MS = 15_000L

        @Volatile
        var running = false
            private set
    }
}
