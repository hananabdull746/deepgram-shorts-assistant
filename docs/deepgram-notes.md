# Deepgram integration notes

Research checked 2026-10-15 against Deepgram's official documentation.

- Prerecorded transcription uses `POST https://api.deepgram.com/v1/listen`, with `Authorization: Token <API_KEY>` and an audio media type for raw-file uploads. The local-file example sends the file body with `--data-binary`; a remote-URL request may use JSON.
- `model=nova-3`, `smart_format=true`, and `utterances=true` provide punctuated text and semantically grouped timestamped utterances. Word results include start/end offsets.
- The docs list a 2 GB file-size maximum for prerecorded files and a 10-minute processing-time limit for synchronous Nova/Base/Enhanced requests. Extracting audio from video is recommended for large video sources.
- With `callback=<public URL>`, Deepgram returns a `request_id` immediately and later POSTs the transcription result to the configured URL. Callback requests are retried up to 10 times with 30-second intervals when the receiver returns a non-2xx status.

References:

- https://developers.deepgram.com/docs/pre-recorded-audio
- https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded
- https://developers.deepgram.com/docs/using-callbacks-to-return-transcripts-to-your-server
- https://deepgram.com/learn/working-with-timestamps-utterances-and-speaker-diarization-in-deepgram
