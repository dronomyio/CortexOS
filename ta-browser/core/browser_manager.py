import asyncio
import os
import tempfile
import time
from dotenv import load_dotenv
from browserbase import Browserbase


from playwright.async_api import async_playwright as playwright
from playwright.async_api import BrowserContext, ConsoleMessage
from playwright.async_api import Page
from playwright.async_api import Playwright

from core.utils.custom_exceptions import BrowserNavigationError
from core.utils.notification import NotificationManager

from core.utils.dom_mutation_observer import dom_mutation_change_detected
from core.utils.dom_mutation_observer import handle_navigation_for_mutation_observer
from core.utils.js_helper import beautify_plan_message
from core.utils.js_helper import escape_js_message

from core.utils.message_type import MessageType
from enum import Enum
from typing import Optional

from core.utils.logger import Logger
logger = Logger()

load_dotenv()

# Ensures that playwright does not wait for font loading when taking screenshots.
os.environ["PW_TEST_SCREENSHOT_NO_FONTS_READY"] = "1"

class PlaywrightManager:
    def __new__(cls, *args, **kwargs):
        # Remove singleton pattern
        return super().__new__(cls)
    
    def __init__(self, 
             browser_type: str = "chromium", 
             headless: bool = False, 
             gui_input_mode: bool = True, 
             screenshots_dir: str = "", 
             take_screenshots: bool = False,  # Changed to false by default
             job_ID: str = "",
             prefix: str = "task",
             start_url: str = "https://google.com"
            ):
        """
        Initializes the PlaywrightManager with simplified parameters.
        """
        self._homepage = start_url
        self._instance = None
        self._playwright = None
        self._browser_context = None
        self.__async_initialize_done = False
        self._take_screenshots = False  # Default to false
        self._screenshots_dir = None
        
        self.browser_type = browser_type
        self.isheadless = headless 
        self.notification_manager = NotificationManager()
        self.user_response_event = asyncio.Event()
        self.ui_manager = None
        self.browserbase = None
        self.browserbase_client = None
        self.bb_live_url = None

        
        self.job_ID = job_ID

        # Initialize external browser services if available
        
        if os.getenv('BROWSERBASE_API_KEY'):
            api_key = os.getenv('BROWSERBASE_API_KEY')
            project_id = os.getenv('BROWSERBASE_PROJECT_ID')
        
            self.browserbase = None
            self._browser = None
            self.bb_live_url = None
            if api_key:
                if not project_id:
                    raise ValueError("BROWSERBASE_PROJECT_ID not present in env")
                client = Browserbase(api_key=api_key)
                self.browserbase_client = client
                self.browserbase = client.sessions.create(project_id=project_id)
                assert self.browserbase.id is not None
                self.bb_live_url = client.sessions.debug(self.browserbase.id).debugger_fullscreen_url
                assert self.browserbase.status == "RUNNING", f"Session status is {self.browserbase.status}"

    async def async_initialize(self):
        """
        Asynchronously initialize necessary components and handlers.
        """
        try:
            if self.__async_initialize_done:
                return

            # Step 1: Start Playwright
            await self.start_playwright()
            
            # Step 2: Create browser context
            await self.ensure_browser_context()
            
            # Step 3: Setup handlers
            await self.setup_handlers()
            
            # Step 4: Navigate to homepage
            await self.go_to_homepage()
            
            self.__async_initialize_done = True
            logger.info("PlaywrightManager initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize PlaywrightManager: {str(e)}")
            raise RuntimeError(f"Failed to initialize PlaywrightManager: {str(e)}") from e

    async def ensure_browser_context(self):
        """
        Ensure that a browser context exists, creating it if necessary.
        """
        if self._browser_context is None:
            await self.create_browser_context()

    async def setup_handlers(self):
        """
        Setup various handlers after the browser context has been ensured.
        """
        if not self.ui_manager:
            return
        await self.set_overlay_state_handler()
        await self.set_user_response_handler()
        await self.set_navigation_handler()

    async def start_playwright(self):
        """
        Starts the Playwright instance if it hasn't been started yet. This method is idempotent.
        """
        try:
            if not self._playwright:
                logger.info("Starting new playwright instance")
                self._playwright = await playwright().start()
                if not self._playwright:
                    raise RuntimeError("Failed to start playwright")
                logger.info("Playwright instance started successfully")

        except Exception as e:
            logger.error(f"Failed to start playwright: {str(e)}")
            raise RuntimeError(f"Failed to start playwright: {str(e)}") from e

    async def stop_playwright(self):
        """
        Stops the Playwright instance and resets it to None. This method should be called to clean up resources.
        """
        logger.info("Beginning Playwright cleanup process...")
        
        try:
            # Browser context cleanup
            if self._browser_context is not None:
                try:
                    logger.info("Cleaning up browser context...")
                    await self._browser_context.close()
                    logger.info("Browser context cleaned up successfully")
                except Exception as e:
                    logger.warn(f"Non-critical error during context cleanup: {str(e)}")
                finally:
                    self._browser_context = None

            # Close browser instance
            if getattr(self, '_browser', None):
                try:
                    logger.info("Closing browser instance...")
                    await self._browser.close()
                    logger.info("Browser instance closed successfully")
                except Exception as e:
                    logger.warn(f"Non-critical error during browser cleanup: {str(e)}")

            # Stop Playwright
            if self._playwright is not None:
                try:
                    logger.info("Stopping Playwright instance...")
                    await self._playwright.stop()
                    logger.info("Playwright instance stopped successfully")
                except Exception as e:
                    logger.warn(f"Non-critical error during playwright cleanup: {str(e)}")
                finally:
                    self._playwright = None

        except Exception as e:
            logger.error(f"Error during Playwright cleanup: {str(e)}")
        finally:
            logger.info("Playwright cleanup process completed")

    async def navigate_to_url(self, url: str):
        """Navigate to the specified URL"""
        try:
            # Add URL protocol if missing
            url = "https://" + url if not url.startswith(('http://', 'https://')) else url
            page = await self.get_current_page()
            await page.goto(url)
            logger.debug(f"Successfully navigated to {url}")
        except Exception as e:
            logger.error(f"Failed to navigate to {url}: {str(e)}")
            raise

    async def create_browser_context(self):
        """
        Creates a new browser context using the specified or default browser directory.
        """
        try:
            # Initialize Playwright if needed
            if not self._playwright:
                await self.start_playwright()
            if not self._playwright:
                raise RuntimeError("Failed to initialize Playwright")

            user_dir = tempfile.mkdtemp() 

            if self.browser_type == "chromium":
                if self.browserbase:
                    try:
                        logger.debug("Connecting to browserbase...")
                        self._browser = await self._playwright.chromium.connect_over_cdp(self.browserbase.connect_url)
                        self._browser_context = self._browser.contexts[0]
                        logger.info("Successfully connected to browserbase")
                        return
                    except Exception as browserbase_error:
                        logger.warn(f"browserbase failed: {browserbase_error}")
            
                # Fallback to local browser with retries
                logger.debug("Launching local browser")
                await self._launch_local_browser_with_retry(user_dir)
            else:
                raise ValueError(f"Unsupported browser type: {self.browser_type}")
            
        except Exception as e:
            logger.error(f"Browser context creation failed: {str(e)}")
            raise

    async def _launch_local_browser_with_retry(self, initial_user_dir: str):
        """Local browser launch with directory retry logic"""
        try:
            self._browser_context = await self._launch_local_browser(initial_user_dir)
        except Exception as e:
            if "Target page, context or browser has been closed" in str(e):
                new_user_dir = tempfile.mkdtemp()
                logger.warn(f"Retrying with new directory: {new_user_dir}")
                try:
                    if not self._playwright:
                        await self.start_playwright()
                    self._browser_context = await self._launch_local_browser(new_user_dir)
                except Exception as retry_error:
                    logger.error(f"Retry failed: {retry_error}")
                    raise
            elif "Chromium distribution 'chrome' is not found" in str(e):
                raise ValueError(
                    "Chrome not installed. Install Google Chrome or run 'playwright install chrome'"
                ) from None
            else:
                raise

    async def _launch_local_browser(self, user_dir: str) -> BrowserContext:
        """Launch local Chrome instance with automation mitigation"""
        if not self._playwright:
            raise RuntimeError("Playwright not initialized")

        return await self._playwright.chromium.launch_persistent_context(
            user_dir,
            bypass_csp=True,
            channel="chrome",
            headless=self.isheadless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-session-crashed-bubble",
                "--disable-infobars",
            ],
            no_viewport=True,
        )
    
    async def get_browser_context(self):
        """
        Returns the existing browser context, or creates a new one if it doesn't exist.
        """
        await self.ensure_browser_context()
        return self._browser_context

    async def get_current_url(self) -> str | None:
        """
        Get the current URL of current page

        Returns:
            str | None: The current URL if any.
        """
        try:
            current_page: Page = await self.get_current_page()
            return current_page.url
        except Exception:
            pass
        return None

    async def get_current_page(self) -> Page:
        """
        Get the current page of the browser with improved error handling
        
        Returns:
            Page: The current page if any.
            
        Raises:
            BrowserNavigationError: If browser context is closed or page cannot be created
        """
        try:
            browser: BrowserContext = await self.get_browser_context()
            # Filter out closed pages
            pages: list[Page] = [page for page in browser.pages if not page.is_closed()]
            page: Page | None = pages[-1] if pages else None
            logger.debug(f"Current page: {page.url if page else None}")
            if page is not None:
                return page
            else:
                page:Page = await browser.new_page() # type: ignore
                return page
        except Exception:
                logger.error("Browser context was closed. Creating a new one.")
                try:
                    page: Page = await browser.new_page()
                    return page
                except Exception as e:
                    logger.error(f"Failed to create new page: {e}")
                    raise BrowserNavigationError("Browser context was closed or new page creation failed")
                    
        except Exception as e:
            logger.error(f"Browser context error: {e}")
            raise BrowserNavigationError(f"Critical browser error: {str(e)}")

    async def close_all_tabs(self, keep_first_tab: bool = True):
        """
        Closes all tabs in the browser context, except for the first tab if `keep_first_tab` is set to True.

        Args:
            keep_first_tab (bool, optional): Whether to keep the first tab open. Defaults to True.
        """
        browser_context = await self.get_browser_context()
        pages: list[Page] = browser_context.pages #type: ignore
        pages_to_close: list[Page] = pages[1:] if keep_first_tab else pages # type: ignore
        for page in pages_to_close: # type: ignore
            await page.close() # type: ignore

    async def close_except_specified_tab(self, page_to_keep: Page):
        """
        Closes all tabs in the browser context, except for the specified tab.

        Args:
            page_to_keep (Page): The Playwright page object representing the tab that should remain open.
        """
        browser_context = await self.get_browser_context()
        for page in browser_context.pages: # type: ignore
            if page != page_to_keep:  # Check if the current page is not the one to keep
                await page.close() # type: ignore

    async def go_to_homepage(self):
        await self.navigate_to_url(self._homepage)

    async def set_navigation_handler(self):
        if not self.ui_manager:
            return
        page:Page = await self.get_current_page()
        page.on("domcontentloaded", self.ui_manager.handle_navigation) # type: ignore
        page.on("domcontentloaded", handle_navigation_for_mutation_observer) # type: ignore
        await page.expose_function("dom_mutation_change_detected", dom_mutation_change_detected) # type: ignore

    async def set_overlay_state_handler(self):
        logger.debug("Setting overlay state handler")
        context = await self.get_browser_context()
        await context.expose_function('overlay_state_changed', self.overlay_state_handler) # type: ignore
        await context.expose_function('show_steps_state_changed',self.show_steps_state_handler) # type: ignore

    async def overlay_state_handler(self, is_collapsed: bool):
        if not self.ui_manager:
            return
        page = await self.get_current_page()
        self.ui_manager.update_overlay_state(is_collapsed)
        if not is_collapsed:
            await self.ui_manager.update_overlay_chat_history(page)

    async def show_steps_state_handler(self, show_details: bool):
        if not self.ui_manager:
            return
        page = await self.get_current_page()
        await self.ui_manager.update_overlay_show_details(show_details, page)

    async def set_user_response_handler(self):
        context = await self.get_browser_context()
        await context.expose_function('user_response', self.receive_user_response) # type: ignore

    async def notify_user(self, message: str, message_type: MessageType = MessageType.STEP):
        if not self.ui_manager:
            return
        """
        Notify the user with a message.

        Args:
            message (str): The message to notify the user with.
            message_type (enum, optional): Values can be 'PLAN', 'QUESTION', 'ANSWER', 'INFO', 'STEP'. Defaults to 'STEP'.
        """

        logger.info(f"Notify user with message: {message}")
        logger.info(f"Message type: {message_type}")

        if message.startswith(":"):
            message = message[1:]

        if message.endswith(","):
            message = message[:-1]

        if message_type == MessageType.PLAN:
            message = beautify_plan_message(message)
            message = "Plan:\n" + message
        elif message_type == MessageType.STEP:
            if "confirm" in message.lower():
                message = "Verify: " + message
            else:
                message = "Next step: " + message
        elif message_type == MessageType.QUESTION:
            message = "Question: " + message
        elif message_type == MessageType.ANSWER:
            message = "Response: " + message

        safe_message = escape_js_message(message)
        self.ui_manager.new_system_message(safe_message, message_type)

        if self.ui_manager.overlay_show_details == False:  # noqa: E712
            if message_type not in (MessageType.PLAN, MessageType.QUESTION, MessageType.ANSWER, MessageType.INFO):
                return

        if self.ui_manager.overlay_show_details == True:  # noqa: E712
            if message_type not in (MessageType.PLAN,  MessageType.QUESTION , MessageType.ANSWER,  MessageType.INFO, MessageType.STEP):
                return

        safe_message_type = escape_js_message(message_type.value)
        try:
            js_code = f"addSystemMessage({safe_message}, is_awaiting_user_response=false, message_type={safe_message_type});"
            page = await self.get_current_page()
            await page.evaluate(js_code)
        except Exception as e:
            logger.error(f"Failed to notify user with message \"{message}\". However, most likey this will work itself out after the page loads: {e}")

        self.notification_manager.notify(message, message_type.value)

    async def highlight_element(self, selector: str, add_highlight: bool):
        try:
            page: Page = await self.get_current_page()
            if add_highlight:
                # Add the 'tawebagent-ui-automation-highlight' class to the element. This class is used to apply the fading border.
                await page.eval_on_selector(selector, '''e => {
                            let originalBorderStyle = e.style.border;
                            e.classList.add('tawebagent-ui-automation-highlight');
                            e.addEventListener('animationend', () => {
                                e.classList.remove('tawebagent-ui-automation-highlight')
                            });}''')
                logger.debug(f"Applied pulsating border to element with selector {selector} to indicate text entry operation")
            else:
                # Remove the 'tawebagent-ui-automation-highlight' class from the element.
                await page.eval_on_selector(selector, "e => e.classList.remove('tawebagent-ui-automation-highlight')")
                logger.debug(f"Removed pulsating border from element with selector {selector} after text entry operation")
        except Exception:
            # This is not significant enough to fail the operation
            pass

    async def receive_user_response(self, response: str):
        self.user_response = response  # Store the response for later use.
        logger.debug(f"Received user response to system prompt: {response}")
        # Notify event loop that the user's response has been received.
        self.user_response_event.set()

    async def prompt_user(self, message: str) -> str:
        if not self.ui_manager:
            return
        """
        Prompt the user with a message and wait for a response.

        Args:
            message (str): The message to prompt the user with.

        Returns:
            str: The user's response.
        """
        logger.debug(f"Prompting user with message: \"{message}\"")
        page = await self.get_current_page()

        await self.ui_manager.show_overlay(page)
        self.log_system_message(message, MessageType.QUESTION) # add the message to history after the overlay is opened to avoid double adding it. add_system_message below will add it

        safe_message = escape_js_message(message)

        js_code = f"addSystemMessage({safe_message}, is_awaiting_user_response=true, message_type='question');"
        await page.evaluate(js_code)

        await self.user_response_event.wait()
        result = self.user_response
        logger.info(f"User prompt reponse to \"{message}\": {result}")
        self.user_response_event.clear()
        self.user_response = ""
        self.ui_manager.new_user_message(result)
        return result

    def log_user_message(self, message: str):
        """
        Log the user's message.

        Args:
            message (str): The user's message to log.
        """
        if not self.ui_manager:
            return
        self.ui_manager.new_user_message(message)

    def log_system_message(self, message: str, type: MessageType = MessageType.STEP):
        """
        Log a system message.

        Args:
            message (str): The system message to log.
        """
        if not self.ui_manager:
            return
        self.ui_manager.new_system_message(message, type)

    async def update_processing_state(self, processing_state: str):
        """
        Update the processing state of the overlay.

        Args:
            is_processing (str): "init", "processing", "done"
        """
        if not self.ui_manager:
            return
        page = await self.get_current_page()

        await self.ui_manager.update_processing_state(processing_state, page)

    async def command_completed(self, command: str, elapsed_time: float | None = None):
        """
        Notify the overlay that the command has been completed.
        """
        logger.debug(f"Command \"{command}\" has been completed. Focusing on the overlay input if it is open.")
        page = await self.get_current_page()
        await self.ui_manager.command_completed(page, command, elapsed_time)