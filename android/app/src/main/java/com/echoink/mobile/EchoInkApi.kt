package com.echoink.mobile

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Talks to the phone server built into the EchoInk desktop app. */
object EchoInkApi {
    class Result(val text: String, val language: String)

    /** The PC could not be reached: it is off or asleep, EchoInk is closed, or there is no network. */
    class UnreachableException(cause: IOException) :
        IOException("Can't reach your PC. Is it on, with EchoInk running and Tailscale connected?", cause)

    /** The PC answered with an error, e.g. 401 for a wrong pairing token. */
    class RejectedException(val status: Int, message: String) : IOException(message)

    /** Sends a WAV recording to the PC and returns what it heard. */
    fun transcribe(settings: Settings, wav: ByteArray): Result {
        val language = URLEncoder.encode(settings.language, "UTF-8")
        val connection = open(settings, "/v1/audio/transcriptions?language=$language", 60_000)
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "audio/wav")
        connection.setFixedLengthStreamingMode(wav.size)
        try {
            connection.outputStream.use { it.write(wav) }
        } catch (e: IOException) {
            throw UnreachableException(e)
        }
        val json = JSONObject(read(connection))
        return Result(json.optString("text"), json.optString("language"))
    }

    /** Throws unless the PC is reachable and accepts the token. */
    fun check(settings: Settings) {
        read(open(settings, "/health", 3_000))
    }

    private fun open(settings: Settings, path: String, readTimeout: Int): HttpURLConnection {
        val connection = URL(settings.serverUrl + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 4_000
        connection.readTimeout = readTimeout
        connection.setRequestProperty("Authorization", "Bearer ${settings.token}")
        return connection
    }

    private fun read(connection: HttpURLConnection): String {
        val body: String
        val code: Int
        try {
            code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            body = stream?.bufferedReader()?.use { it.readText() } ?: ""
        } catch (e: IOException) {
            throw UnreachableException(e)
        }
        if (code == 401) throw RejectedException(code, "The PC rejected the pairing token; scan the QR code again")
        if (code !in 200..299) {
            val message = runCatching { JSONObject(body).getJSONObject("error").getString("message") }
                .getOrDefault(body.take(200))
            throw RejectedException(code, "PC error $code: $message")
        }
        return body
    }
}
