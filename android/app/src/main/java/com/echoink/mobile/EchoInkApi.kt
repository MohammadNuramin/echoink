package com.echoink.mobile

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Talks to the phone server built into the EchoInk desktop app. */
object EchoInkApi {
    class Result(val text: String, val language: String)

    /** Sends a WAV recording to the PC and returns what it heard. */
    fun transcribe(settings: Settings, wav: ByteArray): Result {
        val language = URLEncoder.encode(settings.language, "UTF-8")
        val connection = open(settings, "/v1/audio/transcriptions?language=$language")
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "audio/wav")
        connection.setFixedLengthStreamingMode(wav.size)
        connection.outputStream.use { it.write(wav) }
        val json = JSONObject(read(connection))
        return Result(json.optString("text"), json.optString("language"))
    }

    /** Throws with a readable message unless the PC is reachable and accepts the token. */
    fun check(settings: Settings) {
        read(open(settings, "/health"))
    }

    private fun open(settings: Settings, path: String): HttpURLConnection {
        val connection = URL(settings.serverUrl + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 5_000
        connection.readTimeout = 60_000
        connection.setRequestProperty("Authorization", "Bearer ${settings.token}")
        return connection
    }

    private fun read(connection: HttpURLConnection): String {
        val code = try {
            connection.responseCode
        } catch (e: IOException) {
            throw IOException("Cannot reach your PC. Is Tailscale on here, and is EchoInk running with Phone access on? (${e.message})", e)
        }
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
        if (code == 401) throw IOException("The PC rejected the pairing token; scan the QR code again")
        if (code !in 200..299) {
            val message = runCatching { JSONObject(body).getJSONObject("error").getString("message") }
                .getOrDefault(body.take(200))
            throw IOException("PC error $code: $message")
        }
        return body
    }
}
