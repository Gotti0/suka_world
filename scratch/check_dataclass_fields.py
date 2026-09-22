from youtube_transcript_api._transcripts import FetchedTranscript
from dataclasses import fields
print([f.name for f in fields(FetchedTranscript)])
