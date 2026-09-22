from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()
# We don't need a real video ID to just check the return type's structure if we can find it in the module
from youtube_transcript_api._transcripts import FetchedTranscript
print(dir(FetchedTranscript))
