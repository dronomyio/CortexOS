import os
import time
from typing import Annotated
from typing import Any
import json
from playwright.async_api import Page
import re
from pydantic_ai import Agent
# from core.agents.explainer_agent import explainer_agent
from core.browser_manager import PlaywrightManager
from core.utils.dom_helper import wait_for_non_loading_dom_state
from core.utils.get_detailed_accessibility_tree import do_get_accessibility_info
from core.utils.ui_messagetype import MessageType
from core.utils.openai_msg_parser import AgentConversationHandler, ConversationStorage
from core.utils.openai_msg_parser import extract_explainer_data
# _explainer_storage = ConversationStorage(job_id=browser_manager.job_ID, file_name="explainer.json")

from config import PROJECT_SOURCE_ROOT

from core.utils.logger import Logger
logger = Logger()
_explainer_conversation_handler = AgentConversationHandler()


def get_explainer_storage(bc: PlaywrightManager) -> ConversationStorage:
    return ConversationStorage(job_id=bc.job_ID, file_name="explainer.json")

def extract_and_parse_json(response_str):
    # First try parsing the entire string as JSON
    try:
        return json.loads(response_str)
    except json.JSONDecodeError:
        # If that fails, try to extract JSON using regex
        try:
            # Pattern to find JSON objects containing "expected_field_info"
            pattern = r'(\{(?:[^{}]|(?:\{[^{}]*\}))*"expected_field_info"(?:[^{}]|(?:\{[^{}]*\}))*\})'
            json_match = re.search(pattern, response_str)
            
            if json_match:
                json_str = json_match.group(0)
                return json.loads(json_str)
        except (AttributeError, json.JSONDecodeError) as e:
            logger.error(f"Error extracting JSON with regex: {e}")
    
    # If all parsing attempts fail, return default empty structure
    return {"expected_field_info": {}}



async def get_dom_texts_func(bc:PlaywrightManager) -> Annotated[str | None, "The text content of the DOM"]:
    """
    Retrieves the text content of the active page's DOM.

    Parameters
    ----------
    current_step : str
        The current step in the workflow being executed. This helps track and log the context 
        of the DOM extraction operation.

    Returns
    -------
    str | None
        The text content of the page including image alt texts.

    Raises
    ------
    ValueError
        If no active page is found.
    """
    logger.debug("Executing Get DOM Text Command")
    
    start_time = time.time()

    # Create and use the PlaywrightManager
    browser_manager = bc
    page = await browser_manager.get_current_page()
    if page is None:
        raise ValueError('No active page found. OpenURL command opens a new page.')

    await wait_for_non_loading_dom_state(page, 2000)
    # Get filtered text content including alt text from images
    text_content = await get_filtered_text_content(page)
    file_path = os.path.join(PROJECT_SOURCE_ROOT, 'temp', f'task_{browser_manager.job_ID}', 'text_only_dom.txt')
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(text_content)

    elapsed_time = time.time() - start_time
    logger.debug(f"Get DOM Text Command executed in {elapsed_time:.2f} seconds")
    await browser_manager.notify_user(
        f"Fetched the text content of the DOM and saved to {file_path}",
        message_type=MessageType.ACTION
    )
    
    return text_content


async def get_dom_field_func(
    bc,
    current_step: Annotated[str, "The current step in the workflow being executed"],
    info: Annotated[str, "The information requested by the tool call"],
    screenshot_path: Annotated[str | None, "Path to the pre-action screenshot"] = None,
    custom_prompt: Annotated[str | None, "Custom prompt to be passed to the explainer agent"] = None,
    agent: Annotated[Agent, "The explainer agent instance"] = None
) -> Annotated[dict[str, Any] | None, "The interactive fields data from the DOM"]:
    """
    Retrieves all interactive fields from the active page's DOM.
    """
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Executing Get DOM Fields Command")
    start_time = time.time()

    browser_manager = bc
    page = await browser_manager.get_current_page()
    if page is None:
        raise ValueError('No active page found. OpenURL command opens a new page.')

    logger.debug(f"[{time.strftime('%H:%M:%S')}] Waiting for non-loading DOM state")
    await wait_for_non_loading_dom_state(page, 2000)
    wait_time = time.time()
    logger.debug(f"[{time.strftime('%H:%M:%S')}] DOM state wait complete, took {wait_time - start_time:.2f} seconds")

    # Get all interactive elements
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Starting accessibility info capture")
    raw_data = await do_get_accessibility_info(page, browser_manager, only_input_fields=False)
    capture_time = time.time()
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Accessibility info captured, took {capture_time - wait_time:.2f} seconds")

    # Construct the base prompt
    prompt = (
        f"DOM tree : {raw_data}\n"
        f"current step : {current_step}\n"
        f"Information requested by the user : {info}"
        f"{custom_prompt if custom_prompt else ''}"
    )

    explainer_agent = agent
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Running explainer agent")
    explainer_response = await explainer_agent.run(prompt)
    explainer_time = time.time()
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Explainer agent completed, took {explainer_time - capture_time:.2f} seconds")

    try:
        if isinstance(explainer_response.data, str):
            parsed_data = extract_and_parse_json(explainer_response.data)
            processed_data = parsed_data.get("expected_field_info", {})
        else:
            processed_data = explainer_response.data.expected_field_info
    except Exception as e:
        logger.error(f"Error extracting expected field info: {str(e)}", exc_info=True)
        processed_data = {"expected_field_info": {}}

    logger.debug(f"[{time.strftime('%H:%M:%S')}] Saving conversation history")
    try:
        _explainer_conversation_handler.add_explainer_message(processed_data, prompt)
        messages = _explainer_conversation_handler.get_conversation_history()
        explainer_storage = ConversationStorage(job_id=bc.job_ID, file_name="explainer.json")
        saved_path = explainer_storage.save_conversation(messages, prefix="task")
        logger.info(f"Saved Explainer Agent messages to: {saved_path}")
    except Exception as e:
        logger.error(f"Failed to save Explainer Agent messages: {str(e)}", exc_info=True)
    save_time = time.time()
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Conversation history saved, took {save_time - explainer_time:.2f} seconds")

    total_time = save_time - start_time
    logger.debug(f"[{time.strftime('%H:%M:%S')}] Get DOM Fields Command completed, total time: {total_time:.2f} seconds")
    return processed_data


async def get_filtered_text_content(page: Page) -> str:
    """Helper function to get filtered text content from the page."""
    text_content = await page.evaluate("""
        () => {
            // Array of query selectors to filter out
            const selectorsToFilter = ['#tawebagent-overlay'];

            // Store the original visibility values to revert later
            const originalStyles = [];

            // Hide the elements matching the query selectors
            selectorsToFilter.forEach(selector => {
                const elements = document.querySelectorAll(selector);
                elements.forEach(element => {
                    originalStyles.push({ element: element, originalStyle: element.style.visibility });
                    element.style.visibility = 'hidden';
                });
            });

            // Get the text content of the page
            let textContent = document?.body?.innerText || document?.documentElement?.innerText || "";

            // Get all the alt text from images on the page
            let altTexts = Array.from(document.querySelectorAll('img')).map(img => img.alt);
            altTexts = "Other Alt Texts in the page: " + altTexts.join(' ');

            // Revert the visibility changes
            originalStyles.forEach(entry => {
                entry.element.style.visibility = entry.originalStyle;
            });
            
            return textContent + " " + altTexts;
        }
    """)
    return text_content


def prompt_constructor(inputs: str) -> str:
    """Helper function to construct a prompt string with system prompt and inputs."""
    return f"Inputs :\n{inputs}"