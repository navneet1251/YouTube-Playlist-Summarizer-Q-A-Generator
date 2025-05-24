import streamlit as st
from dotenv import load_dotenv
import os
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from googleapiclient.discovery import build
import json
from datetime import datetime
import re
import time

# ✅ Must be the first Streamlit command
st.set_page_config("📺 YouTube Playlist Summarizer", layout="wide")

# Load environment variables
load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

genai.configure(api_key=GOOGLE_API_KEY)

# 📌 Improved prompts
SUMMARY_PROMPT = """
You are an expert notetaker. Create highly structured, easy-to-read, and detailed notes based on the transcript.
Make it resemble perfect lecture notes. Use markdown formatting with:
- Clear section headings (###)
- Bullet points and subpoints
- Key insights, definitions, and takeaways
- Use bold or italics to highlight important concepts
Keep it concise but information-rich (300–500 words). Transcript:
"""

QA_PROMPT = """
You are a smart assistant. Based on the transcript below, generate 5–10 high-quality Q&A pairs that cover key ideas.
Use a markdown list format:
- **Question:** ...
  **Answer:** ...
Transcript:
"""

# Extract playlist ID
def get_playlist_id(url):
    match = re.search(r"(?:list=)([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None

# Get video list
def extract_playlist_items(playlist_url):
    try:
        playlist_id = get_playlist_id(playlist_url)
        if not playlist_id:
            raise ValueError("Invalid playlist URL")

        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        videos = []
        next_page_token = None

        while True:
            request = youtube.playlistItems().list(
                part="contentDetails,snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()

            for item in response.get("items", []):
                video_id = item["contentDetails"].get("videoId")
                title = item["snippet"].get("title")
                if video_id and title:
                    videos.append({"videoId": video_id, "title": title})

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        return videos
    except Exception as e:
        st.error(f"Failed to fetch playlist: {e}")
        return []

@st.cache_data(show_spinner=False)
def extract_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-US', 'hi'])
        return " ".join([seg["text"] for seg in transcript])
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception as e:
        st.warning(f"Transcript error for {video_id}: {e}")
        return None

# Generate with Gemini with fallback
def generate_with_gemini(prompt, text, retries=3, model_names=["gemini-1.5-flash", "gemini-1.5-pro"]):
    for model_name in model_names:
        model = genai.GenerativeModel(model_name)
        for attempt in range(retries):
            try:
                response = model.generate_content(prompt + text)
                return response.text.strip()
            except Exception as e:
                err_str = str(e)
                if "429" in err_str:
                    delay_match = re.search(r"retry_delay\s*{\s*seconds:\s*(\d+)", err_str)
                    delay_seconds = int(delay_match.group(1)) if delay_match else 60
                    st.warning(f"⚠️ Rate limit hit. Waiting {delay_seconds} seconds...")
                    time.sleep(delay_seconds)
                else:
                    st.error(f"❌ {model_name} failed: {err_str}")
                    break
    return None

# Download buttons
def create_download_buttons(content, video_id, content_type):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{video_id}_{content_type}_{timestamp}"
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(f"💾 {content_type.capitalize()} (TXT)", data=content, file_name=f"{filename}.txt")
    with col2:
        st.download_button(f"📁 {content_type.capitalize()} (JSON)",
                           data=json.dumps({content_type: content}, indent=2),
                           file_name=f"{filename}.json", mime="application/json")

# UI
st.title("📺 YouTube Playlist Summarizer & Q&A Generator")

playlist_url = st.text_input("🔗 Paste Playlist URL:", placeholder="https://www.youtube.com/playlist?list=...")

with st.sidebar:
    st.header("⚙️ Settings")
    max_videos = st.slider("Number of videos to process", 1, 20, 10)
    process_option = st.radio("Select videos", ["All", "Choose manually"])
    throttle_delay = st.slider("API cooldown (seconds)", 0, 60, 10)

if playlist_url:
    with st.spinner("🔍 Fetching playlist..."):
        video_items = extract_playlist_items(playlist_url)

    if not video_items:
        st.error("🚫 No videos found or invalid playlist.")
    else:
        st.success(f"✅ Found {len(video_items)} videos!")
        titles = [f"{i+1}. {vid['title']}" for i, vid in enumerate(video_items)]

        if process_option == "Choose manually":
            selected_indices = st.multiselect("Pick videos to summarize", range(len(titles)), format_func=lambda i: titles[i])
            selected_videos = [video_items[i] for i in selected_indices]
        else:
            selected_videos = video_items[:max_videos]

        if st.button("🚀 Start Processing"):
            all_notes = {}
            all_qas = {}

            for idx, vid in enumerate(selected_videos):
                video_id = vid["videoId"]
                title = vid["title"]
                st.markdown("---")
                st.subheader(f"🎥 {title} ({idx+1}/{len(selected_videos)})")

                st.markdown(f"[▶️ Watch on YouTube](https://youtu.be/{video_id})")
                st.image(f"https://img.youtube.com/vi/{video_id}/0.jpg", width=320)

                with st.spinner("📝 Extracting transcript..."):
                    transcript = extract_transcript(video_id)

                if not transcript:
                    st.warning("🚫 No transcript found. Skipping...")
                    continue

                tab1, tab2, tab3 = st.tabs(["📘 Summary Notes", "❓ Q&A", "📄 Transcript"])
                with tab1:
                    st.info("📚 Generating structured notes...")
                    summary = generate_with_gemini(SUMMARY_PROMPT, transcript)
                    if summary:
                        st.markdown(summary)
                        all_notes[title] = summary
                        create_download_buttons(summary, video_id, "summary")
                    else:
                        st.error("❌ Summary generation failed.")

                with tab2:
                    st.info("🤔 Creating questions...")
                    qas = generate_with_gemini(QA_PROMPT, transcript)
                    if qas:
                        st.markdown(qas)
                        all_qas[title] = qas
                        create_download_buttons(qas, video_id, "qa")
                    else:
                        st.error("❌ Q&A generation failed.")

                with tab3:
                    st.text_area("📄 Raw Transcript", value=transcript[:3000] + ("..." if len(transcript) > 3000 else ""), height=300)
                    create_download_buttons(transcript, video_id, "transcript")

                if throttle_delay > 0 and idx < len(selected_videos) - 1:
                    st.info(f"🕒 Waiting {throttle_delay} seconds to avoid quota issues...")
                    time.sleep(throttle_delay)

            st.markdown("## 📦 Download Everything")
            col1, col2 = st.columns(2)
            with col1:
                if all_notes:
                    st.download_button("📥 Download All Summaries", data=json.dumps(all_notes, indent=2),
                                       file_name="all_summaries.json", mime="application/json")
            with col2:
                if all_qas:
                    st.download_button("📥 Download All Q&As", data=json.dumps(all_qas, indent=2),
                                       file_name="all_qas.json", mime="application/json")
            st.balloons()
