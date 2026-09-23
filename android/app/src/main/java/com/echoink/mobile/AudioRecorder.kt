package com.echoink.mobile

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Records 16 kHz mono 16-bit PCM (what the EchoInk models expect) and returns it as WAV. */
class AudioRecorder {
    private val pcm = ByteArrayOutputStream()
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    @Volatile
    var isRecording = false
        private set

    @SuppressLint("MissingPermission") // The activity asks for RECORD_AUDIO before the mic starts.
    fun start() {
        val minBuffer = AudioRecord.getMinBufferSize(RATE, CHANNEL, ENCODING)
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, CHANNEL, ENCODING, maxOf(minBuffer, RATE)
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            throw IllegalStateException("the microphone is in use or blocked")
        }
        synchronized(pcm) { pcm.reset() }
        recorder.startRecording()
        record = recorder
        isRecording = true
        thread = Thread({
            val buffer = ByteArray(RATE / 5) // 100 ms of 16-bit audio
            while (isRecording) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) synchronized(pcm) { pcm.write(buffer, 0, read) }
            }
        }, "echoink-recorder").also { it.start() }
    }

    /** Stops recording and returns the audio as a WAV file. */
    fun stop(): ByteArray {
        isRecording = false
        thread?.join(1_000)
        thread = null
        record?.run {
            stop()
            release()
        }
        record = null
        return wav(synchronized(pcm) { pcm.toByteArray() })
    }

    private fun wav(data: ByteArray): ByteArray {
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray()).putInt(36 + data.size).put("WAVE".toByteArray())
        header.put("fmt ".toByteArray()).putInt(16)
            .putShort(1.toShort()) // PCM
            .putShort(1.toShort()) // mono
            .putInt(RATE).putInt(RATE * 2)
            .putShort(2.toShort()) // bytes per frame
            .putShort(16.toShort()) // bits per sample
        header.put("data".toByteArray()).putInt(data.size)
        return header.array() + data
    }

    companion object {
        const val RATE = 16_000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
    }
}
