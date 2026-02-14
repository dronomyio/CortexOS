"""
Local Embedding Service for Video Understanding Application
Uses sentence-transformers to generate embeddings without OpenAI API
"""

from sentence_transformers import SentenceTransformer
from typing import List
import numpy as np

class LocalEmbeddingService:
    """
    Local embedding service using sentence-transformers
    
    Recommended models:
    - all-MiniLM-L6-v2: Fast, 384 dimensions, good for general text
    - all-mpnet-base-v2: Better quality, 768 dimensions, slower
    - paraphrase-multilingual-MiniLM-L12-v2: Multilingual support
    """
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize the local embedding model
        
        Args:
            model_name: HuggingFace model name (default: all-MiniLM-L6-v2)
        """
        print(f"Loading local embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.dimensions = self.model.get_sentence_embedding_dimension()
        print(f"✓ Model loaded. Embedding dimensions: {self.dimensions}")
    
    def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding for a single text
        
        Args:
            text: Input text string
            
        Returns:
            List of floats representing the embedding vector
        """
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()
    
    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generate embeddings for multiple texts efficiently
        
        Args:
            texts: List of input text strings
            batch_size: Number of texts to process at once
            
        Returns:
            List of embedding vectors
        """
        embeddings = self.model.encode(
            texts, 
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True
        )
        return embeddings.tolist()
    
    def get_dimensions(self) -> int:
        """Get the embedding vector dimensions"""
        return self.dimensions


# Example usage
if __name__ == "__main__":
    # Initialize service
    service = LocalEmbeddingService("all-MiniLM-L6-v2")
    
    # Test single embedding
    text = "Never gonna give you up, never gonna let you down"
    embedding = service.embed_text(text)
    print(f"\nSingle text embedding:")
    print(f"  Text: {text}")
    print(f"  Embedding dimensions: {len(embedding)}")
    print(f"  First 5 values: {embedding[:5]}")
    
    # Test batch embedding
    texts = [
        "Never gonna give you up",
        "Never gonna let you down",
        "Never gonna run around and desert you"
    ]
    embeddings = service.embed_batch(texts)
    print(f"\nBatch embedding:")
    print(f"  Number of texts: {len(texts)}")
    print(f"  Number of embeddings: {len(embeddings)}")
    print(f"  Embedding dimensions: {len(embeddings[0])}")
