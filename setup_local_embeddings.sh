#!/bin/bash

# Setup Local Embeddings for Video Understanding Application
# This script integrates sentence-transformers for local embedding generation

set -e

echo "=========================================="
echo "Setup Local Embeddings Integration"
echo "=========================================="

# Step 1: Add sentence-transformers to requirements
echo ""
echo "Step 1: Adding sentence-transformers to requirements..."
cd /Users/macmachine/tools/drone_project_idea/Blogs/Video_understanding/CortexON

if ! grep -q "sentence-transformers" cortex_on/requirements.txt; then
    echo "sentence-transformers>=2.2.2" >> cortex_on/requirements.txt
    echo "✓ Added sentence-transformers to requirements.txt"
else
    echo "✓ sentence-transformers already in requirements.txt"
fi

# Step 2: Copy local embedding scripts
echo ""
echo "Step 2: Copying local embedding scripts..."
cp ./local_embeddings.py cortex_on/video_scripts/
cp ./weaviate_ingest_local.py cortex_on/video_scripts/
echo "✓ Scripts copied"

# Step 3: Update orchestrator_agent.py to use local embeddings
echo ""
echo "Step 3: Updating orchestrator_agent.py..."

# Backup original
cp cortex_on/agents/orchestrator_agent.py cortex_on/agents/orchestrator_agent.py.backup_local

# Update the weaviate_ingest.py call to use weaviate_ingest_local.py
python3 << 'EOF'
with open('cortex_on/agents/orchestrator_agent.py', 'r') as f:
    content = f.read()

# Replace weaviate_ingest.py with weaviate_ingest_local.py
content = content.replace(
    'subprocess.run(["python3", "/app/video_scripts/weaviate_ingest.py"',
    'subprocess.run(["python3", "/app/video_scripts/weaviate_ingest_local.py"'
)

# Remove --openai-model and --dimensions parameters (not needed for local)
content = content.replace(
    ', "--json", "/data/downloads/snippets_with_transcripts.json", "--video-id", video_id], capture_output=True, text=True, timeout=300, check=True)',
    ', "--json", "/data/downloads/snippets_with_transcripts.json", "--video-id", video_id, "--embedding-model", "all-MiniLM-L6-v2"], capture_output=True, text=True, timeout=300, check=True)'
)

with open('cortex_on/agents/orchestrator_agent.py', 'w') as f:
    f.write(content)

print("✓ Updated orchestrator_agent.py to use local embeddings")
EOF

# Step 4: Update .env to remove OpenAI requirement (optional)
echo ""
echo "Step 4: Updating .env file..."
echo "# Local embeddings enabled - OpenAI API not required for video indexing" >> .env
echo "USE_LOCAL_EMBEDDINGS=true" >> .env
echo "LOCAL_EMBEDDING_MODEL=all-MiniLM-L6-v2" >> .env
echo "✓ Updated .env"

# Step 5: Rebuild Docker image
echo ""
echo "Step 5: Rebuilding Docker image with local embeddings..."
docker-compose stop cortex_on
docker-compose rm -f cortex_on
docker rmi cortexon-cortex_on || true
docker-compose build --no-cache cortex_on

echo ""
echo "=========================================="
echo "✓ Local Embeddings Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Start services: docker-compose up -d cortex_on"
echo "2. Test ingestion: curl -X GET \"http://localhost:8083/agent/chat?task=Ingest%20this%20YouTube%20video%3A%20https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DdQw4w9WgXcQ\""
echo ""
echo "Benefits:"
echo "  - No OpenAI API quota limits"
echo "  - No API costs for embeddings"
echo "  - Faster embedding generation (local GPU if available)"
echo "  - Works offline"
echo ""
echo "Model info:"
echo "  - Model: all-MiniLM-L6-v2"
echo "  - Dimensions: 384"
echo "  - Speed: ~1000 texts/second on CPU"
echo ""
