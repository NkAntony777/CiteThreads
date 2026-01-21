import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(str(Path("e:/vibe_coding_project/CiteThreads/backend").absolute()))

import asyncio
from app.services.storage import project_storage
from app.routers.writing import add_reference, AddReferenceRequest
from app.models import Paper

async def verify():
    print("Listing projects...")
    projects = project_storage.list_projects()
    if not projects:
        print("No projects found. Creating one.")
        metadata = project_storage.create_project(seed_paper_id="test", name="Test Project")
        project_id = metadata.id
    else:
        project_id = projects[0].id
    
    print(f"Using project: {project_id}")
    
    # Add a mock paper to the graph to test find
    project = project_storage.get_project(project_id)
    paper = Paper(id="test_paper", title="Test Paper")
    project.graph.nodes.append(paper)
    project_storage.save_graph(project_id, project.graph)
    
    print("Testing add_reference...")
    request = AddReferenceRequest(paper_id="test_paper", source="graph")
    
    try:
        # We call the function directly. Note: it's an async function.
        # It calls project_storage.get_project(project_id) internally.
        result = await add_reference(project_id, request)
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify())
