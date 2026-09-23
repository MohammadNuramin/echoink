package com.echoink.mobile

import android.content.Context
import java.io.File

/** Recordings made while the PC was unreachable, kept on the phone until they can be sent. */
class PendingRecordings(context: Context) {
    private val dir = File(context.filesDir, "pending").apply { mkdirs() }

    val count: Int
        get() = files().size

    fun add(wav: ByteArray) {
        File(dir, "${System.currentTimeMillis()}.wav").writeBytes(wav)
        files().dropLast(MAX_KEPT).forEach { it.delete() } // keep the newest
    }

    /** The oldest waiting recording, sent first. */
    fun oldest(): File? = files().firstOrNull()

    private fun files(): List<File> = dir.listFiles()?.sortedBy { it.name } ?: emptyList()

    companion object {
        private const val MAX_KEPT = 20
    }
}
