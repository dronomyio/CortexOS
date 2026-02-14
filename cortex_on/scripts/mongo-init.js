// MongoDB initialization script for video understanding system
// This script creates the database, collections, and indexes

db = db.getSiblingDB('video_understanding');

// Create collections
db.createCollection('videos');
db.createCollection('transcripts');
db.createCollection('keyframes');
db.createCollection('processing_jobs');
db.createCollection('queries');
db.createCollection('quality_metrics');

print('Collections created successfully');

// Create indexes for videos collection
db.videos.createIndex({ video_id: 1 }, { unique: true });
db.videos.createIndex({ "processing.status": 1, created_at: -1 });
db.videos.createIndex({ "source.upload_date": -1 });
db.videos.createIndex({ tags: 1 });
db.videos.createIndex({ "quality.transcript_confidence": 1 });

print('Videos indexes created');

// Create indexes for transcripts collection
db.transcripts.createIndex({ video_id: 1, segment_index: 1 }, { unique: true });
db.transcripts.createIndex({ video_id: 1, start_time: 1, end_time: 1 });
db.transcripts.createIndex({ "chunks.weaviate_id": 1 });
db.transcripts.createIndex({ text: "text" });

print('Transcripts indexes created');

// Create indexes for keyframes collection
db.keyframes.createIndex({ video_id: 1, timestamp: 1 });
db.keyframes.createIndex({ video_id: 1, keyframe_index: 1 }, { unique: true });
db.keyframes.createIndex({ "visual_elements.objects": 1 });
db.keyframes.createIndex({ embedding_id: 1 });

print('Keyframes indexes created');

// Create indexes for processing_jobs collection
db.processing_jobs.createIndex({ job_id: 1 }, { unique: true });
db.processing_jobs.createIndex({ video_id: 1, created_at: -1 });
db.processing_jobs.createIndex({ status: 1, priority: -1, created_at: 1 });
db.processing_jobs.createIndex({ "agent.name": 1, status: 1 });

print('Processing jobs indexes created');

// Create indexes for queries collection
db.queries.createIndex({ query_id: 1 }, { unique: true });
db.queries.createIndex({ user_id: 1, created_at: -1 });
db.queries.createIndex({ video_ids: 1, created_at: -1 });
db.queries.createIndex({ question: "text" });
db.queries.createIndex({ "answer.confidence": 1 });

print('Queries indexes created');

// Create indexes for quality_metrics collection
db.quality_metrics.createIndex({ video_id: 1, metric_type: 1, measured_at: -1 });
db.quality_metrics.createIndex({ metric_type: 1, score: 1 });
db.quality_metrics.createIndex({ "user_feedback.user_id": 1 });

print('Quality metrics indexes created');

// Create a sample video document for testing
db.videos.insertOne({
  video_id: "sample_video_001",
  title: "Sample Video for Testing",
  description: "This is a sample video document",
  duration: 120.0,
  source: {
    platform: "youtube",
    url: "https://youtube.com/watch?v=sample",
    uploader: "Test Channel",
    uploader_id: "UC_test",
    upload_date: new Date(),
    view_count: 0,
    like_count: 0,
    comment_count: 0
  },
  files: {
    video_path: "/data/downloads/sample_video_001.mp4",
    video_size: 0,
    format: "mp4",
    resolution: "1920x1080",
    fps: 30
  },
  processing: {
    status: "pending",
    stages: {},
    last_updated: new Date(),
    error: null
  },
  quality: {
    transcript_confidence: 0.0,
    transcript_word_count: 0,
    transcript_language: "en",
    audio_quality_score: 0.0,
    has_music: false,
    has_multiple_speakers: false
  },
  tags: ["sample", "test"],
  topics: ["testing"],
  created_at: new Date(),
  updated_at: new Date(),
  indexed_at: null
});

print('Sample document inserted');

print('MongoDB initialization completed successfully');
