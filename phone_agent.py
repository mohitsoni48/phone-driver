import os
import json
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from qwen_vl_agent import QwenVLAgent


class PhoneAgent:
    """
    Phone automation agent using Qwen3-VL for visual understanding and ADB for control.
    
    This agent:
    - Captures screenshots from Android devices via ADB
    - Uses Qwen3-VL to analyze screens and determine actions
    - Executes actions through ADB commands
    - Tracks context and action history
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the phone agent.
        
        Args:
            config: Configuration dictionary
        """
        # Default configuration
        default_config = {
            'device_id': None,  # Auto-detect first device if None
            'screen_width': 1080,  # Must match your device
            'screen_height': 2340,  # Must match your device
            'screenshot_dir': './screenshots',
            'max_retries': 3,
            'model_name': 'Qwen/Qwen3-VL-30B-A3B-Instruct',
            'use_flash_attention': False,
            'temperature': 0.1,
            'max_tokens': 512,
            'step_delay': 1.5,  # Seconds to wait after each action
            'enable_visual_debug': False,  # Save annotated screenshots
        }
        
        self.config = default_config
        if config:
            self.config.update(config)
        
        # Session context
        self.context = {
            'previous_actions': [],
            'current_app': "Home",
            'task_request': "",
            'session_id': datetime.now().strftime("%Y%m%d_%H%M%S"),
            'screenshots': []
        }
        
        # Setup logging
        self._setup_logging()
        
        # Initialize directories
        self._setup_directories()
        
        # Check ADB connection
        self._check_adb_connection()
        
        # Initialize Qwen3-VL agent
        logging.info("Initializing Qwen3-VL agent...")
        self.vl_agent = QwenVLAgent(
            use_flash_attention=self.config.get('use_flash_attention', False),
            temperature=self.config['temperature'],
            max_tokens=self.config['max_tokens'],
        )
        logging.info("Phone agent ready")
    
    def _setup_logging(self):
        """Configure logging for this session."""
        log_file = f"phone_agent_{self.context['session_id']}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        logging.info(f"Session started: {self.context['session_id']}")
    
    def _setup_directories(self):
        """Create necessary directories."""
        Path(self.config['screenshot_dir']).mkdir(parents=True, exist_ok=True)
        logging.info(f"Screenshots directory: {self.config['screenshot_dir']}")
    
    def _check_adb_connection(self):
        """Verify ADB connection and get device info."""
        try:
            # List devices
            result = subprocess.run(
                ["adb", "devices"],
                check=True,
                capture_output=True,
                text=True
            )
            
            # Auto-detect device if not specified
            if self.config['device_id'] is None:
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    device_info = lines[1].split('\t')
                    if len(device_info) > 0 and device_info[1].strip() == 'device':
                        self.config['device_id'] = device_info[0].strip()
                        logging.info(f"Auto-detected device: {self.config['device_id']}")
                    else:
                        raise Exception("No authorized device found")
                else:
                    raise Exception("No devices connected")
            
            # Test connection
            self._run_adb_command("shell echo 'Connected'")
            logging.info("✓ ADB connection verified")
            
            # Get actual screen resolution
            self._verify_screen_resolution()
            
        except subprocess.CalledProcessError as e:
            logging.error(f"ADB error: {e}")
            raise Exception(
                "Failed to connect via ADB. Ensure USB debugging is enabled and device is authorized."
            )
    
    def _verify_screen_resolution(self):
        """Verify the configured screen resolution matches the device."""
        try:
            result = self._run_adb_command("shell wm size")
            # Output format: "Physical size: 1080x2340"
            if "Physical size:" in result:
                size_str = result.split("Physical size:")[1].strip()
                width, height = map(int, size_str.split('x'))
                
                if width != self.config['screen_width'] or height != self.config['screen_height']:
                    logging.warning("=" * 60)
                    logging.warning("RESOLUTION MISMATCH DETECTED!")
                    logging.warning(f"Device actual:    {width} x {height}")
                    logging.warning(f"Config setting:   {self.config['screen_width']} x {self.config['screen_height']}")
                    logging.warning("Please update config.json with correct resolution!")
                    logging.warning("=" * 60)
                    
                    # Update config automatically
                    self.config['screen_width'] = width
                    self.config['screen_height'] = height
                    logging.info(f"Auto-corrected to: {width} x {height}")
                else:
                    logging.info(f"✓ Screen resolution confirmed: {width} x {height}")
        except Exception as e:
            logging.warning(f"Could not verify screen resolution: {e}")
    
    def _run_adb_command(self, command: str) -> str:
        """Execute an ADB command and return output."""
        device_prefix = f"-s {self.config['device_id']}" if self.config['device_id'] else ""
        full_command = f"adb {device_prefix} {command}"
        
        try:
            result = subprocess.run(
                full_command,
                shell=True,
                check=True,
                capture_output=True,
                text=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            logging.error(f"ADB command failed: {command}")
            logging.error(f"Error: {e.stderr}")
            raise
    
    def capture_screenshot(self) -> str:
        """
        Capture a screenshot from the device.
        
        Returns:
            Path to the saved screenshot
        """
        timestamp = int(time.time())
        screenshot_path = os.path.join(
            self.config['screenshot_dir'],
            f"screen_{self.context['session_id']}_{timestamp}.png"
        )
        
        try:
            # Capture and transfer screenshot
            self._run_adb_command("shell screencap -p /sdcard/screenshot.png")
            self._run_adb_command(f"pull /sdcard/screenshot.png {screenshot_path}")
            self._run_adb_command("shell rm /sdcard/screenshot.png")
            
            logging.info(f"Screenshot captured: {screenshot_path}")
            self.context['screenshots'].append(screenshot_path)
            return screenshot_path
            
        except Exception as e:
            logging.error(f"Screenshot capture failed: {e}")
            raise
    
    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an action on the device.
        
        Args:
            action: Action dictionary from Qwen3-VL
            
        Returns:
            Result dictionary with success status
        """
        try:
            action_type = action['action']
            logging.info(f"Executing: {action_type}")
            
            # Handle task completion
            if action_type == 'terminate':
                status = action.get('status', 'success')
                message = action.get('message', 'Task complete')
                logging.info(f"✓ Task {status}: {message}")
                return {
                    'success': True,
                    'action': action,
                    'task_complete': True
                }
            
            # Handle each action type
            if action_type == 'tap':
                self._execute_tap(action)
            
            elif action_type == 'swipe':
                self._execute_swipe(action)
            
            elif action_type == 'type':
                self._execute_type(action)
            
            elif action_type == 'wait':
                self._execute_wait(action)
            
            else:
                raise ValueError(f"Unknown action type: {action_type}")
            
            # Record action in history
            self.context['previous_actions'].append({
                'action': action_type,
                'timestamp': time.time(),
                'elementName': action.get('observation', '')[:50]  # Brief description
            })
            
            # Standard delay after action
            time.sleep(self.config['step_delay'])
            
            return {
                'success': True,
                'action': action,
                'task_complete': False
            }
            
        except Exception as e:
            logging.error(f"Action execution failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'action': action,
                'task_complete': False
            }
    
    def _execute_tap(self, action: Dict[str, Any]):
        """Execute a tap action."""
        if 'coordinates' not in action:
            raise ValueError("Tap action missing coordinates")
        
        # Get normalized coordinates
        norm_x, norm_y = action['coordinates']
        
        # Convert to pixel coordinates
        x = int(norm_x * self.config['screen_width'])
        y = int(norm_y * self.config['screen_height'])
        
        # Clamp to screen bounds
        x = max(0, min(x, self.config['screen_width'] - 1))
        y = max(0, min(y, self.config['screen_height'] - 1))
        
        logging.info(f"Tapping at ({x}, {y}) [normalized: ({norm_x:.3f}, {norm_y:.3f})]")
        self._run_adb_command(f"shell input tap {x} {y}")
    
    def _execute_swipe(self, action: Dict[str, Any]):
        """Execute a swipe action."""
        direction = action.get('direction', 'up')
        
        # Calculate swipe coordinates
        center_x = self.config['screen_width'] // 2
        center_y = self.config['screen_height'] // 2
        
        start_x, start_y = center_x, center_y
        
        # Define swipe distances (70% of screen dimension)
        swipe_distance = 0.7
        
        if direction == 'up':
            end_x = center_x
            end_y = int(center_y * (1 - swipe_distance))
        elif direction == 'down':
            end_x = center_x
            end_y = int(center_y * (1 + swipe_distance))
        elif direction == 'left':
            end_x = int(center_x * (1 - swipe_distance))
            end_y = center_y
        elif direction == 'right':
            end_x = int(center_x * (1 + swipe_distance))
            end_y = center_y
        else:
            raise ValueError(f"Invalid swipe direction: {direction}")
        
        logging.info(f"Swiping {direction}: ({start_x}, {start_y}) -> ({end_x}, {end_y})")
        self._run_adb_command(f"shell input swipe {start_x} {start_y} {end_x} {end_y} 300")
    
    def _execute_type(self, action: Dict[str, Any]):
        """Execute a type action."""
        if 'text' not in action:
            raise ValueError("Type action missing text")
        
        text = action['text']
        
        # Check if we tapped a text field recently
        recent_actions = self.context['previous_actions'][-3:]
        tapped_text_field = any(
            a.get('action') == 'tap' for a in recent_actions
        )
        
        if not tapped_text_field:
            logging.warning("Type action without recent tap - may fail")
        
        # Escape and format text for ADB
        escaped_text = text.replace("'", "\\'").replace('"', '\\"')
        escaped_text = escaped_text.replace(" ", "%s")  # ADB requires %s for spaces
        
        logging.info(f"Typing: {text}")
        self._run_adb_command(f'shell input text "{escaped_text}"')
    
    def _execute_wait(self, action: Dict[str, Any]):
        """Execute a wait action."""
        wait_time = action.get('waitTime', 1000) / 1000.0  # Convert ms to seconds
        logging.info(f"Waiting {wait_time:.1f}s...")
        time.sleep(wait_time)
    
    def execute_cycle(self, user_request: str) -> Dict[str, Any]:
        """
        Execute a single interaction cycle.
        
        Args:
            user_request: The user's task request
            
        Returns:
            Result dictionary
        """
        try:
            # Capture screenshot
            screenshot_path = self.capture_screenshot()
            
            # Analyze with Qwen3-VL
            action = self.vl_agent.analyze_screenshot(
                screenshot_path,
                user_request,
                self.context
            )
            
            if not action:
                raise Exception("Failed to get action from model")
            
            # Log model's observation and reasoning
            if 'observation' in action:
                logging.info(f"Model observation: {action['observation']}")
            if 'reasoning' in action:
                logging.info(f"Model reasoning: {action['reasoning']}")
            
            # Execute the action
            result = self.execute_action(action)
            
            return result
            
        except Exception as e:
            logging.error(f"Cycle execution failed: {e}")
            raise
    
    def execute_task(self, user_request: str, max_cycles: int = 15) -> Dict[str, Any]:
        """
        Execute a complete task through multiple cycles.
        
        Args:
            user_request: The user's task description
            max_cycles: Maximum number of action cycles
            
        Returns:
            Task result dictionary
        """
        self.context['task_request'] = user_request
        logging.info("=" * 60)
        logging.info(f"STARTING TASK: {user_request}")
        logging.info("=" * 60)
        
        cycles = 0
        task_complete = False
        last_error = None
        
        while cycles < max_cycles and not task_complete:
            cycles += 1
            logging.info(f"\n--- Cycle {cycles}/{max_cycles} ---")
            
            try:
                result = self.execute_cycle(user_request)
                
                if result.get('task_complete'):
                    task_complete = True
                    logging.info("✓ Task marked complete by agent")
                    break
                
                if not result['success']:
                    last_error = result.get('error', 'Unknown error')
                    logging.warning(f"Action failed: {last_error}")
                    
                    # Retry logic
                    if cycles >= self.config['max_retries']:
                        logging.error("Max retries exceeded")
                        break
                
            except KeyboardInterrupt:
                logging.info("Task interrupted by user")
                raise
            except Exception as e:
                last_error = str(e)
                logging.error(f"Cycle error: {e}")
                
                if cycles >= self.config['max_retries']:
                    break
                
                # Wait before retry
                time.sleep(2)
        
        # Final verification if we hit max cycles
        if cycles >= max_cycles and not task_complete:
            logging.info("Max cycles reached, checking if task is actually complete...")
            screenshot_path = self.capture_screenshot()
            completion_check = self.vl_agent.check_task_completion(
                screenshot_path,
                user_request,
                self.context
            )
            
            if completion_check.get('complete'):
                task_complete = True
                logging.info(f"✓ Task verified complete: {completion_check.get('reason')}")
        
        # Summary
        logging.info("\n" + "=" * 60)
        if task_complete:
            logging.info(f"✓ TASK COMPLETED in {cycles} cycles")
            success = True
        else:
            logging.info(f"✗ TASK INCOMPLETE after {cycles} cycles")
            if last_error:
                logging.info(f"Last error: {last_error}")
            success = False
        logging.info("=" * 60)
        
        return {
            'success': success,
            'cycles': cycles,
            'task_complete': task_complete,
            'context': self.context,
            'screenshots': self.context['screenshots']
        }


if __name__ == "__main__":
    # Simple test
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python phone_agent.py 'your task here'")
        sys.exit(1)
    
    task = ' '.join(sys.argv[1:])
    
    # Load config
    config_path = 'config.json'
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        config = {}
    
    # Run task
    agent = PhoneAgent(config)
    result = agent.execute_task(task)
    
    if result['success']:
        print(f"\n✓ Task completed in {result['cycles']} cycles")
    else:
        print(f"\n✗ Task failed after {result['cycles']} cycles")