"""Load the external workspace implementation only in platform contract tests."""
from pathlib import Path
import sys

platform_root = Path(__file__).parents[1] / 'workspace/robocasa'
sys.path.insert(0, str(platform_root))
from runtime import RoboCasa, RoboCasaConfig
from runtime.backend import MotionCancelled
from runtime.environment import ROBOT_TYPE
from runtime import environment as environment_module
