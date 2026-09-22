from youtube_transcript_api import YouTubeTranscriptApi
import inspect

print(f"Fetch: {inspect.signature(YouTubeTranscriptApi.fetch)}")
try:
    print(f"List: {inspect.signature(YouTubeTranscriptApi.list)}")
except:
    print("List is not a function or has no signature")

# Let's try to see if it's a class or something else
print(f"Type: {type(YouTubeTranscriptApi)}")
