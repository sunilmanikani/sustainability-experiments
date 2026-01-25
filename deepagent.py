import pandas as pd
import os
import traceback
from typing import List, Dict, Optional

# LangChain Imports
from langchain_core.tools import tool
from langchain_community.document_loaders import PyPDFLoader
from langgraph.checkpoint.memory import MemorySaver 

# Deep Agents Imports
from deepagents import create_deep_agent

# Import your LLM configuration
try:
    from getllm import llm
except ImportError:
    print("Error: 'getllm.py' not found. Please ensure your LLM setup file is present.")
    exit(1)

# ==========================================
# CONFIGURATION
# ==========================================
CONFIG = {
    "PDF_FILENAME": "any_company.pdf",
    "OUTPUT_FILENAME": "any_company_deep_agent_v2.xlsx",
    "RECURSION_LIMIT": 30, # Lower limit needed because the agent is smarter now
    "THREAD_ID": "session_v2"
}

# ==========================================
# 0. GLOBAL CACHE (Optimization)
# ==========================================
# We use a global cache to avoid re-parsing the PDF for every tool call.
# In a production app, this would be a proper vector store or database.
PDF_CACHE = {}

def _get_pdf_pages(filename: str):
    """Helper to load PDF once and cache it."""
    if filename not in PDF_CACHE:
        print(f"[SYSTEM] 📂 Loading PDF '{filename}' into memory map... (This happens once)")
        if not os.path.exists(filename):
            raise FileNotFoundError(f"File {filename} does not exist.")
        
        loader = PyPDFLoader(filename)
        # Load all pages. Since we only access them via index later, this is fine.
        PDF_CACHE[filename] = loader.load()
        print(f"[SYSTEM] ✅ Loaded {len(PDF_CACHE[filename])} pages.")
    
    return PDF_CACHE[filename]

# ==========================================
# 1. INTELLIGENT TOOLS
# ==========================================

@tool
def search_pdf_for_pages(filename: str, keywords: str) -> str:
    """
    Scans the ENTIRE PDF to find pages that contain the specific keywords.
    Use this FIRST to locate where the data tables are (e.g., search for 'Greenhouse gas emissions' or 'Scope 1').
    Returns a list of Page Numbers and a brief snippet of context.
    """
    print(f"\n[TOOL] 🔍 Scanning '{filename}' for keywords: '{keywords}'...")
    try:
        pages = _get_pdf_pages(filename)
        hits = []
        
        # Naive keyword search (in production, use Vector Search)
        query_terms = keywords.lower().split()
        
        for i, page in enumerate(pages):
            content = page.page_content.lower()
            # Simple logic: if all terms are present (loose matching)
            if all(term in content for term in query_terms):
                # Grab a snippet around the keyword
                snippet_idx = content.find(query_terms[0])
                snippet = page.page_content[snippet_idx:snippet_idx+150].replace('\n', ' ')
                hits.append(f"- Page {i+1}: ...{snippet}...")
        
        if not hits:
            return f"No pages found containing all keywords: '{keywords}'. Try relaxing your search terms."
        
        # Return top 5 hits to avoid context overflow
        result = "Found keywords on the following pages:\n" + "\n".join(hits[:5])
        if len(hits) > 5:
            result += f"\n... (and {len(hits)-5} more pages)"
            
        print(f"[DEBUG] Found {len(hits)} matches.")
        return result

    except Exception as e:
        return f"Error searching PDF: {e}"

@tool
def read_specific_page(filename: str, page_number: int) -> str:
    """
    Reads the FULL content of a SPECIFIC page number.
    Use this AFTER using 'search_pdf_for_pages' to extract the actual data.
    Input 'page_number' should be the actual page number (1-based) returned by the search tool.
    """
    print(f"\n[TOOL] 📖 Reading Page {page_number} of '{filename}'...")
    try:
        pages = _get_pdf_pages(filename)
        
        # Adjust for 0-based index
        idx = page_number - 1
        
        if idx < 0 or idx >= len(pages):
            return f"Error: Page {page_number} is out of range (1-{len(pages)})."
            
        content = pages[idx].page_content
        return f"--- CONTENT OF PAGE {page_number} ---\n{content}"

    except Exception as e:
        return f"Error reading page: {e}"

@tool
def save_emissions_to_excel(data: List[Dict], filename: str = "emissions.xlsx") -> str:
    """
    Saves extracted emissions data to an Excel file.
    Input data must be a list of dictionaries.
    Example: [{"Year": 2023, "Scope 1": 100, "Scope 2": 50, "Unit": "Million tonnes CO2e"}]
    Make sure to include a 'Unit' column if the source data specifies it.
    """
    print(f"\n[TOOL] 💾 Saving data to {filename}...")
    print(f"[DEBUG] Data received: {data}")
    try:
        df = pd.DataFrame(data)
        df.to_excel(filename, index=False)
        return f"Success! Data saved to {filename}"
    except Exception as e:
        return f"Error saving Excel: {e}"

# Define the tools list
tools = [search_pdf_for_pages, read_specific_page, save_emissions_to_excel]

# ==========================================
# 2. DEEP AGENT SETUP
# ==========================================
def run_deep_agent():
    print("\n" + "="*50)
    print("🧠 RUNNING DEEP AGENT V2 (Explorer-Extractor Mode)")
    print("="*50)
    
    memory = MemorySaver()

    deep_agent = create_deep_agent(
        model=llm,
        tools=tools,
        checkpointer=memory
    )

    # We give the agent a high-level goal. Notice we don't tell it WHICH pages to look at.
    # We force it to Plan -> Search -> Read -> Extract -> Save.
    query = (
        f"I need to create a sustainability report from '{CONFIG['PDF_FILENAME']}'.\n"
        "1. Search the document to locate the 'Greenhouse gas emissions' data table. (Look for 'Scope 1' and 'Scope 2').\n"
        "2. Read the specific page(s) containing the table to get the full context.\n"
        "3. Extract the data for the last 5 years available.\n"
        "4. Identify the 'Unit' of measurement (e.g., Million tonnes) and include it as a column.\n"
        f"5. Save to '{CONFIG['OUTPUT_FILENAME']}'."
    )

    config = {
        "recursion_limit": CONFIG["RECURSION_LIMIT"],
        "configurable": {"thread_id": CONFIG["THREAD_ID"]}
    } 
    
    print("Deep Agent Plan (Streaming):")
    inputs = {"messages": [("user", query)]}
    
    try:
        final_response = None
        
        for event in deep_agent.stream(inputs, config=config):
            for key, value in event.items():
                print(f"\n--- Node: {key} ---")
                
                if isinstance(value, dict) and "messages" in value:
                    messages = value["messages"]
                    if isinstance(messages, list) and len(messages) > 0:
                        last_msg = messages[-1]
                        
                        if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                            for tc in last_msg.tool_calls:
                                print(f"🛠️ [TOOL CALL]: {tc['name']} (Args: {tc['args']})")
                        
                        if hasattr(last_msg, 'content') and last_msg.content:
                            preview = last_msg.content[:200].replace('\n', ' ') + "..."
                            print(f"🤖 [AGENT]: {preview}")
                            
                        final_response = last_msg

        if final_response:
            print(f"\n✅ Mission Complete. Response:\n{final_response.content}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    run_deep_agent()