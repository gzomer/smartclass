## Inspiration

Covid-19 has disrupted education and most classes are now online. However, it is very time-consuming to review long lectures and find the relevant content you want to study.

## What it does

Smart Class allows students to easily review lectures by automatically transcribing the recordings, organizing the content, and adding insights in a user-friendly interface. Here is a summary of the main features:

#### Transcription and Search
Students can easily review lectures transcriptions and start playing from any point they want to. It is also very easy to find relevant parts by using the integrated search feature.

#### Related content
Relevant keywords are extracted using Natural Language Processing and links to Wikipedia are added for each keyword in the transcription so that students can find additional learning material.

#### Topics
Messages are grouped by topics so that students can focus on studying specific parts they want to improve on.

#### Relevant Questions
Students can search for which questions were asked either by the lecturer or the students.

#### Action items and Follow-ups
Students can also see which actions and follow-ups need to be done, such as project deadlines or homework they need to finish on a certain date

#### Speakers diarization
The lectures are separated by each speaker so that students can review content separately.

## How we built it

1. Student pastes lecture link
2. Video is downloaded
3. Video is converted to mp3
4. Symbl Audio Async API is called using the processed mp3 file with diarization enabled
5. The lecture description, title, and the `conversationId` and `jobId` are saved in a MongoDB database
6. The job status is monitored until it is completed
7. After the job is completed, the following information is extracted:
	* Conversation info
	* Transcription (messages)
	* Questions
	* Topics
	* Action items
	* Follow-ups
8. Keywords are extracted using NLTK
9. Links are added to messages containing the keywords
10. Messages are grouped by speakers
11. Messages are grouped by topics
12. The timestamp for each message is computed using the conversation start date as a reference
13. Context messages are added for actions, follow-ups and questions

## What's next for Smart Class
- Allow more media types
- Add more related content
- Add inline related content