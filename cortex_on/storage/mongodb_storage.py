"""
MongoDB Storage Layer for Video Understanding System

This module provides a unified interface for storing and retrieving
video metadata, transcripts, keyframes, and processing state in MongoDB.
"""

import os
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError
import logfire


@dataclass
class VideoMetadata:
    """Video metadata structure"""
    video_id: str
    title: str
    description: str
    duration: float
    source: Dict[str, Any]
    files: Dict[str, Any]
    processing: Dict[str, Any]
    quality: Dict[str, Any]
    tags: List[str]
    topics: List[str]
    created_at: datetime
    updated_at: datetime
    indexed_at: Optional[datetime] = None


@dataclass
class TranscriptSegment:
    """Transcript segment structure"""
    video_id: str
    segment_index: int
    start_time: float
    end_time: float
    duration: float
    text: str
    words: List[Dict[str, Any]]
    confidence: float
    language: str
    speaker_id: Optional[int]
    chunks: List[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime


@dataclass
class Keyframe:
    """Keyframe structure"""
    video_id: str
    keyframe_index: int
    timestamp: float
    frame_number: int
    description: str
    visual_elements: Dict[str, Any]
    image: Dict[str, Any]
    embedding_id: Optional[str]
    quality_score: float
    perceptual_hash: str
    created_at: datetime


@dataclass
class ProcessingJob:
    """Processing job structure"""
    job_id: str
    job_type: str
    video_id: str
    status: str
    priority: int
    agent: Dict[str, Any]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    max_retries: int
    hitl_interactions: List[Dict[str, Any]]
    depends_on: List[str]
    blocks: List[str]


@dataclass
class Query:
    """User query structure"""
    query_id: str
    user_id: str
    session_id: str
    question: str
    question_normalized: str
    video_ids: List[str]
    collection_name: str
    search_results: Dict[str, Any]
    answer: Dict[str, Any]
    metrics: Dict[str, Any]
    feedback: Optional[Dict[str, Any]]
    created_at: datetime
    completed_at: Optional[datetime]


@dataclass
class QualityMetric:
    """Quality metric structure"""
    video_id: str
    metric_type: str
    score: float
    score_details: Dict[str, Any]
    computed_by: str
    computation_method: str
    user_feedback: Optional[Dict[str, Any]]
    measured_at: datetime
    created_at: datetime


class MongoDBStorage:
    """
    MongoDB storage layer for video understanding system
    
    Provides CRUD operations for all collections with proper indexing,
    error handling, and connection management.
    """
    
    def __init__(
        self,
        connection_string: Optional[str] = None,
        database_name: str = "video_understanding"
    ):
        """
        Initialize MongoDB connection
        
        Args:
            connection_string: MongoDB connection string (defaults to env var)
            database_name: Database name
        """
        self.connection_string = connection_string or os.getenv(
            "MONGODB_URI",
            "mongodb://mongodb:27017/"
        )
        self.database_name = database_name
        
        try:
            self.client: MongoClient = MongoClient(self.connection_string)
            self.db: Database = self.client[database_name]
            
            # Initialize collections
            self.videos: Collection = self.db.videos
            self.transcripts: Collection = self.db.transcripts
            self.keyframes: Collection = self.db.keyframes
            self.processing_jobs: Collection = self.db.processing_jobs
            self.queries: Collection = self.db.queries
            self.quality_metrics: Collection = self.db.quality_metrics
            
            # Create indexes
            self._create_indexes()
            
            logfire.info(f"Connected to MongoDB: {database_name}")
            
        except PyMongoError as e:
            logfire.error(f"Failed to connect to MongoDB: {e}")
            raise
    
    def _create_indexes(self):
        """Create all necessary indexes for optimal performance"""
        try:
            # Videos collection indexes
            self.videos.create_index([("video_id", ASCENDING)], unique=True)
            self.videos.create_index([("processing.status", ASCENDING), ("created_at", DESCENDING)])
            self.videos.create_index([("source.upload_date", DESCENDING)])
            self.videos.create_index([("tags", ASCENDING)])
            self.videos.create_index([("quality.transcript_confidence", ASCENDING)])
            
            # Transcripts collection indexes
            self.transcripts.create_index([("video_id", ASCENDING), ("segment_index", ASCENDING)], unique=True)
            self.transcripts.create_index([("video_id", ASCENDING), ("start_time", ASCENDING), ("end_time", ASCENDING)])
            self.transcripts.create_index([("chunks.weaviate_id", ASCENDING)])
            self.transcripts.create_index([("text", TEXT)])
            
            # Keyframes collection indexes
            self.keyframes.create_index([("video_id", ASCENDING), ("timestamp", ASCENDING)])
            self.keyframes.create_index([("video_id", ASCENDING), ("keyframe_index", ASCENDING)], unique=True)
            self.keyframes.create_index([("visual_elements.objects", ASCENDING)])
            self.keyframes.create_index([("embedding_id", ASCENDING)])
            
            # Processing jobs collection indexes
            self.processing_jobs.create_index([("job_id", ASCENDING)], unique=True)
            self.processing_jobs.create_index([("video_id", ASCENDING), ("created_at", DESCENDING)])
            self.processing_jobs.create_index([("status", ASCENDING), ("priority", DESCENDING), ("created_at", ASCENDING)])
            self.processing_jobs.create_index([("agent.name", ASCENDING), ("status", ASCENDING)])
            
            # Queries collection indexes
            self.queries.create_index([("query_id", ASCENDING)], unique=True)
            self.queries.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
            self.queries.create_index([("video_ids", ASCENDING), ("created_at", DESCENDING)])
            self.queries.create_index([("question", TEXT)])
            self.queries.create_index([("answer.confidence", ASCENDING)])
            
            # Quality metrics collection indexes
            self.quality_metrics.create_index([("video_id", ASCENDING), ("metric_type", ASCENDING), ("measured_at", DESCENDING)])
            self.quality_metrics.create_index([("metric_type", ASCENDING), ("score", ASCENDING)])
            self.quality_metrics.create_index([("user_feedback.user_id", ASCENDING)])
            
            logfire.info("MongoDB indexes created successfully")
            
        except PyMongoError as e:
            logfire.error(f"Failed to create indexes: {e}")
            raise
    
    # ==================== VIDEO OPERATIONS ====================
    
    def insert_video(self, video: VideoMetadata) -> str:
        """
        Insert a new video document
        
        Args:
            video: VideoMetadata object
            
        Returns:
            Inserted document ID
        """
        try:
            video_dict = asdict(video)
            result = self.videos.insert_one(video_dict)
            logfire.info(f"Inserted video: {video.video_id}")
            return str(result.inserted_id)
        except DuplicateKeyError:
            logfire.warning(f"Video already exists: {video.video_id}")
            raise ValueError(f"Video {video.video_id} already exists")
        except PyMongoError as e:
            logfire.error(f"Failed to insert video: {e}")
            raise
    
    def get_video(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get video by video_id
        
        Args:
            video_id: Video identifier
            
        Returns:
            Video document or None
        """
        try:
            return self.videos.find_one({"video_id": video_id})
        except PyMongoError as e:
            logfire.error(f"Failed to get video: {e}")
            raise
    
    def update_video_status(
        self,
        video_id: str,
        stage: str,
        status: str,
        **kwargs
    ) -> bool:
        """
        Update video processing status
        
        Args:
            video_id: Video identifier
            stage: Processing stage (download, transcription, indexing, etc.)
            status: Status (pending, processing, completed, failed)
            **kwargs: Additional fields to update in the stage
            
        Returns:
            True if updated successfully
        """
        try:
            update_dict = {
                f"processing.stages.{stage}.status": status,
                **{f"processing.stages.{stage}.{k}": v for k, v in kwargs.items()}
            }
            update_dict["processing.last_updated"] = datetime.now(timezone.utc)
            update_dict["updated_at"] = datetime.now(timezone.utc)
            
            result = self.videos.update_one(
                {"video_id": video_id},
                {"$set": update_dict}
            )
            return result.modified_count > 0
        except PyMongoError as e:
            logfire.error(f"Failed to update video status: {e}")
            raise
    
    def find_videos_by_status(
        self,
        status: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Find videos by processing status
        
        Args:
            status: Processing status to filter by
            limit: Maximum number of results
            
        Returns:
            List of video documents
        """
        try:
            return list(self.videos.find(
                {"processing.status": status}
            ).sort("created_at", DESCENDING).limit(limit))
        except PyMongoError as e:
            logfire.error(f"Failed to find videos: {e}")
            raise
    
    # ==================== TRANSCRIPT OPERATIONS ====================
    
    def insert_transcript_segment(self, segment: TranscriptSegment) -> str:
        """Insert a transcript segment"""
        try:
            segment_dict = asdict(segment)
            result = self.transcripts.insert_one(segment_dict)
            return str(result.inserted_id)
        except DuplicateKeyError:
            logfire.warning(f"Transcript segment already exists: {segment.video_id}:{segment.segment_index}")
            raise ValueError(f"Segment already exists")
        except PyMongoError as e:
            logfire.error(f"Failed to insert transcript segment: {e}")
            raise
    
    def get_transcript_segments(
        self,
        video_id: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Get transcript segments for a video, optionally filtered by time range
        
        Args:
            video_id: Video identifier
            start_time: Optional start time filter
            end_time: Optional end time filter
            
        Returns:
            List of transcript segments
        """
        try:
            query = {"video_id": video_id}
            if start_time is not None or end_time is not None:
                query["$and"] = []
                if start_time is not None:
                    query["$and"].append({"end_time": {"$gte": start_time}})
                if end_time is not None:
                    query["$and"].append({"start_time": {"$lte": end_time}})
            
            return list(self.transcripts.find(query).sort("segment_index", ASCENDING))
        except PyMongoError as e:
            logfire.error(f"Failed to get transcript segments: {e}")
            raise
    
    # ==================== KEYFRAME OPERATIONS ====================
    
    def insert_keyframe(self, keyframe: Keyframe) -> str:
        """Insert a keyframe"""
        try:
            keyframe_dict = asdict(keyframe)
            result = self.keyframes.insert_one(keyframe_dict)
            return str(result.inserted_id)
        except DuplicateKeyError:
            logfire.warning(f"Keyframe already exists: {keyframe.video_id}:{keyframe.keyframe_index}")
            raise ValueError(f"Keyframe already exists")
        except PyMongoError as e:
            logfire.error(f"Failed to insert keyframe: {e}")
            raise
    
    def get_keyframes(self, video_id: str) -> List[Dict[str, Any]]:
        """Get all keyframes for a video"""
        try:
            return list(self.keyframes.find({"video_id": video_id}).sort("timestamp", ASCENDING))
        except PyMongoError as e:
            logfire.error(f"Failed to get keyframes: {e}")
            raise
    
    # ==================== PROCESSING JOB OPERATIONS ====================
    
    def insert_processing_job(self, job: ProcessingJob) -> str:
        """Insert a processing job"""
        try:
            job_dict = asdict(job)
            result = self.processing_jobs.insert_one(job_dict)
            logfire.info(f"Created processing job: {job.job_id}")
            return str(result.inserted_id)
        except DuplicateKeyError:
            logfire.warning(f"Job already exists: {job.job_id}")
            raise ValueError(f"Job {job.job_id} already exists")
        except PyMongoError as e:
            logfire.error(f"Failed to insert processing job: {e}")
            raise
    
    def update_job_status(
        self,
        job_id: str,
        status: str,
        **kwargs
    ) -> bool:
        """Update processing job status"""
        try:
            update_dict = {"status": status}
            update_dict.update(kwargs)
            
            result = self.processing_jobs.update_one(
                {"job_id": job_id},
                {"$set": update_dict}
            )
            return result.modified_count > 0
        except PyMongoError as e:
            logfire.error(f"Failed to update job status: {e}")
            raise
    
    def get_pending_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending jobs sorted by priority"""
        try:
            return list(self.processing_jobs.find(
                {"status": "pending"}
            ).sort([("priority", DESCENDING), ("created_at", ASCENDING)]).limit(limit))
        except PyMongoError as e:
            logfire.error(f"Failed to get pending jobs: {e}")
            raise
    
    # ==================== QUERY OPERATIONS ====================
    
    def insert_query(self, query: Query) -> str:
        """Insert a user query"""
        try:
            query_dict = asdict(query)
            result = self.queries.insert_one(query_dict)
            return str(result.inserted_id)
        except PyMongoError as e:
            logfire.error(f"Failed to insert query: {e}")
            raise
    
    def update_query_result(
        self,
        query_id: str,
        answer: Dict[str, Any],
        metrics: Dict[str, Any]
    ) -> bool:
        """Update query with answer and metrics"""
        try:
            result = self.queries.update_one(
                {"query_id": query_id},
                {"$set": {
                    "answer": answer,
                    "metrics": metrics,
                    "completed_at": datetime.now(timezone.utc)
                }}
            )
            return result.modified_count > 0
        except PyMongoError as e:
            logfire.error(f"Failed to update query result: {e}")
            raise
    
    def get_user_queries(
        self,
        user_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get query history for a user"""
        try:
            return list(self.queries.find(
                {"user_id": user_id}
            ).sort("created_at", DESCENDING).limit(limit))
        except PyMongoError as e:
            logfire.error(f"Failed to get user queries: {e}")
            raise
    
    # ==================== QUALITY METRICS OPERATIONS ====================
    
    def insert_quality_metric(self, metric: QualityMetric) -> str:
        """Insert a quality metric"""
        try:
            metric_dict = asdict(metric)
            result = self.quality_metrics.insert_one(metric_dict)
            return str(result.inserted_id)
        except PyMongoError as e:
            logfire.error(f"Failed to insert quality metric: {e}")
            raise
    
    def get_quality_metrics(
        self,
        video_id: str,
        metric_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get quality metrics for a video"""
        try:
            query = {"video_id": video_id}
            if metric_type:
                query["metric_type"] = metric_type
            
            return list(self.quality_metrics.find(query).sort("measured_at", DESCENDING))
        except PyMongoError as e:
            logfire.error(f"Failed to get quality metrics: {e}")
            raise
    
    # ==================== ANALYTICS OPERATIONS ====================
    
    def get_processing_stats(self) -> Dict[str, Any]:
        """Get overall processing statistics"""
        try:
            pipeline = [
                {"$group": {
                    "_id": "$processing.status",
                    "count": {"$sum": 1}
                }}
            ]
            results = list(self.videos.aggregate(pipeline))
            return {r["_id"]: r["count"] for r in results}
        except PyMongoError as e:
            logfire.error(f"Failed to get processing stats: {e}")
            raise
    
    def get_average_job_duration(self, job_type: str) -> float:
        """Get average duration for a job type"""
        try:
            pipeline = [
                {"$match": {"job_type": job_type, "status": "completed"}},
                {"$group": {
                    "_id": None,
                    "avg_duration": {"$avg": "$duration_seconds"}
                }}
            ]
            results = list(self.processing_jobs.aggregate(pipeline))
            return results[0]["avg_duration"] if results else 0.0
        except PyMongoError as e:
            logfire.error(f"Failed to get average job duration: {e}")
            raise
    
    def close(self):
        """Close MongoDB connection"""
        try:
            self.client.close()
            logfire.info("MongoDB connection closed")
        except PyMongoError as e:
            logfire.error(f"Failed to close MongoDB connection: {e}")
