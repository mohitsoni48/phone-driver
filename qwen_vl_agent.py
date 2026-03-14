# qwen_vl_agent.py
import json
import logging
import re
from typing import Any, Dict, List, Optional

import torch
from PIL import Image
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor  # NOT MoeFor
#from transformers import Qwen3VLMoeForConditionalGeneration, AutoProcessor - This is only for the MoE Variants!!!
from qwen_vl_utils import process_vision_info
import warnings

# To supress these warnings you can uncomment the following two lines
# warnings.filterwarnings('ignore', message='.*Flash Efficient attention.*')
# warnings.filterwarnings('ignore', message='.*Mem Efficient attention.*')


class QwenVLAgent:
    """
    Vision-Language agent using Qwen3-VL-30B-A3B-Instruct for mobile GUI automation.
    Uses the official mobile_use function calling format.
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-VL-8B-Instruct",
        device_map: str = "auto",
        dtype: Optional[torch.dtype] = None,
        use_flash_attention: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> None:
        """Initialize the Qwen3-VL agent."""
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

        logging.info(f"Loading Qwen3-VL model: {model_name}")

        if dtype is None:
            dtype = torch.bfloat16

        # Build model kwargs once; load once
        model_kwargs: Dict[str, Any] = dict(
            torch_dtype=dtype,
            device_map=device_map,
            low_cpu_mem_usage=True,
            # Only for Strix Halo with 96gb set in bios
            #max_memory={0: "90GiB"},
        )

        if use_flash_attention:
            try:
                import flash_attn  # noqa: F401
                model_kwargs["attn_implementation"] = "flash_attention_2"
                logging.info("Flash Attention 2 enabled")
            except Exception:
                logging.warning("flash_attn not installed; using default attention")

        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            model_name, **model_kwargs
        )
        self.processor = AutoProcessor.from_pretrained(model_name)
        # For MoE Models You need to change to self.model=Qwen3VLMoeForConditionalGeneration.from_pretrained
        # System prompt matching official format
        self.system_prompt = """# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type": "function", "function": {"name": "mobile_use", "description": "Use a touchscreen to interact with a mobile device, and take screenshots.\n* This is an interface to a mobile device with touchscreen. You can perform actions like clicking, typing, swiping, etc.\n* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions.\n* The screen's resolution is 999x999.\n* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.", "parameters": {"properties": {"action": {"description": "The action to perform. The available actions are:\n* `click`: Click the point on the screen with coordinate (x, y).\n* `swipe`: Swipe from the starting point with coordinate (x, y) to the end point with coordinates2 (x2, y2).\n* `type`: Input the specified text into the activated input box.\n* `wait`: Wait specified seconds for the change to happen.\n* `terminate`: Terminate the current task and report its completion status.", "enum": ["click", "swipe", "type", "wait", "terminate"], "type": "string"}, "coordinate": {"description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to click. Required only by `action=click` and `action=swipe`. Range: 0-999.", "type": "array"}, "coordinate2": {"description": "(x, y): The end coordinates for swipe. Required only by `action=swipe`. Range: 0-999.", "type": "array"}, "text": {"description": "Required only by `action=type`.", "type": "string"}, "time": {"description": "The seconds to wait. Required only by `action=wait`.", "type": "number"}, "status": {"description": "The status of the task. Required only by `action=terminate`.", "type": "string", "enum": ["success", "failure"]}}, "required": ["action"], "type": "object"}}}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>

Rules:
- Output exactly in the order: Thought, Action, <tool_call>.
- Be brief: one sentence for Thought, one for Action.
- Do not output anything else outside those three parts.
- If finishing, use action=terminate in the tool call.
- For each function call, there must be an "action" key in the "arguments" which denote the type of the action.
- Coordinates are in 999x999 space where (0,0) is top-left and (999,999) is bottom-right."""
        logging.info("Qwen3-VL agent initialized successfully")

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Analyze a phone screenshot and determine the next action."""
        try:
            # Load and resize image to prevent OOM
            image = Image.open(screenshot_path)

            # Resize if too large - keep aspect ratio, max dimension 1280
            max_size = 1280
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
                logging.info(f"Resized image from {Image.open(screenshot_path).size} to {image.size}")

            # Build action history
            history = []
            if context:
                previous_actions = context.get('previous_actions', [])
                for i, act in enumerate(previous_actions[-5:], 1):  # Last 5 actions
                    action_type = act.get('action', 'unknown')
                    element = act.get('elementName', '')
                    history.append(f"Step {i}: {action_type} {element}".strip())

            history_str = "; ".join(history) if history else "No previous actions"

            # Build user query in official format
            user_query = f"""The user query: {user_request}.
Task progress (You have done the following operation on the current device): {history_str}."""

            # Messages in official format
            messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": self.system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_query},
                        {"type": "image", "image": image},
                    ],
                },
            ]

            # Generate response
            action = self._generate_action(messages)

            if action:
                logging.info(f"Generated action: {action.get('action', 'unknown')}")
                logging.debug(f"Full action: {json.dumps(action, indent=2)}")

            return action

        except Exception as e:
            logging.error(f"Error analyzing screenshot: {e}", exc_info=True)
            return None

    def _generate_action(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Generate an action from the model given messages."""
        try:
            # Use processor's chat template
            text = self.processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # Collect image/video inputs
            image_inputs, video_inputs = process_vision_info(messages)

            # >>>>>>>>>> IMPORTANT FIX: avoid empty lists (use None) <<<<<<<<<<
            if not image_inputs:
                image_inputs = None
            if not video_inputs:
                video_inputs = None

            inputs = self.processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,   # None when no videos -> skips video path
                padding=True,
                return_tensors="pt",
            )

            # Move to device
            inputs = inputs.to(self.model.device)

            logging.debug("Generating model response...")

            # Optional: clear cache around generation (works with HIP too)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            with torch.no_grad():
                generated_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_tokens,
                    temperature=self.temperature,
                    do_sample=self.temperature > 0,
                    pad_token_id=self.processor.tokenizer.pad_token_id,
                )

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Trim input tokens from output
            generated_ids_trimmed = [
                out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]

            # Decode
            output_text = self.processor.batch_decode(
                generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]

            logging.debug(f"Model output: {output_text}")

            # Parse action
            action = self._parse_action(output_text)
            return action

        except Exception as e:
            logging.error(f"Error generating action: {e}", exc_info=True)
            return None

    def _parse_action(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse action from model output in official format."""
        try:
            # Extract tool_call XML content
            match = re.search(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', text, re.DOTALL)
            if not match:
                logging.error("No <tool_call> tags found in output")
                logging.debug(f"Output text: {text}")
                return None

            tool_call_json = match.group(1)
            tool_call = json.loads(tool_call_json)

            # Extract arguments
            if 'arguments' not in tool_call:
                logging.error("No 'arguments' in tool_call")
                return None

            args = tool_call['arguments']
            action_type = args.get('action')
            if not action_type:
                logging.error("No 'action' in arguments")
                return None

            # Convert to our internal format
            action: Dict[str, Any] = {'action': action_type}

            # Handle coordinates (convert from 999x999 space to normalized 0-1)
            if 'coordinate' in args:
                coord = args['coordinate']
                action['coordinates'] = [coord[0] / 999.0, coord[1] / 999.0]

            if 'coordinate2' in args:
                coord2 = args['coordinate2']
                action['coordinate2'] = [coord2[0] / 999.0, coord2[1] / 999.0]

            # Handle swipe - convert to direction for compatibility
            if action_type == 'swipe' and 'coordinates' in action and 'coordinate2' in action:
                start = action['coordinates']
                end = action['coordinate2']
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                if abs(dy) > abs(dx):
                    action['direction'] = 'down' if dy > 0 else 'up'
                else:
                    action['direction'] = 'right' if dx > 0 else 'left'

            # Map action names
            if action_type == 'click':
                action['action'] = 'tap'  # our internal name

            # Copy other fields
            if 'text' in args:
                action['text'] = args['text']
            if 'time' in args:
                action['waitTime'] = int(float(args['time']) * 1000)  # ms
            if 'status' in args:
                action['status'] = args['status']
                action['message'] = f"Task {args['status']}"

            # Extract thought/action description
            thought_match = re.search(r'Thought:\s*(.+?)(?:\n|$)', text)
            action_match = re.search(r'Action:\s*(.+?)(?:\n|$)', text)
            if thought_match:
                action['reasoning'] = thought_match.group(1).strip().strip('"')
            if action_match:
                action['observation'] = action_match.group(1).strip().strip('"')

            # Validate essentials
            if action['action'] == 'tap' and 'coordinates' not in action:
                logging.error("Tap action missing coordinates")
                return None
            if action['action'] == 'type' and 'text' not in action:
                logging.error("Type action missing text")
                return None

            return action

        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON from tool_call: {e}")
            logging.debug(f"Text: {text}")
            return None
        except Exception as e:
            logging.error(f"Error parsing action: {e}")
            logging.debug(f"Text: {text}")
            return None

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Ask the model if the task has been completed."""
        try:
            # Load and resize image
            image = Image.open(screenshot_path)
            max_size = 1280
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            completion_query = f"""The user query: {user_request}.

You have completed {len(context.get('previous_actions', []))} actions.

Look at the current screen and determine: Has the task been completed successfully?

If complete, use action=terminate with status="success".
If not complete, explain what still needs to be done and use action=terminate with status="failure"."""  # noqa: E501

            messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": self.system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": completion_query},
                        {"type": "image", "image": image},
                    ],
                },
            ]

            action = self._generate_action(messages)

            if action and action.get('action') == 'terminate':
                return {
                    "complete": action.get('status') == 'success',
                    "reason": action.get('message', ''),
                    "confidence": 0.9 if action.get('status') == 'success' else 0.7,
                }

            return {"complete": False, "reason": "Unable to verify", "confidence": 0.0}

        except Exception as e:
            logging.error(f"Error checking completion: {e}")
            return {"complete": False, "reason": f"Error: {str(e)}", "confidence": 0.0}
