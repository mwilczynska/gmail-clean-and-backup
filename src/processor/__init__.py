"""Email processing module for extraction, reconstruction, and replacement."""

from src.processor.backup import BackupManager
from src.processor.batch import BatchProcessor
from src.processor.extractor import AttachmentExtractor
from src.processor.reconstructor import EmailReconstructor
from src.processor.replacer import EmailReplacer
from src.processor.transaction import TransactionManager
from src.processor.validator import ReconstructionValidator

__all__ = [
    "AttachmentExtractor",
    "BackupManager",
    "EmailReconstructor",
    "ReconstructionValidator",
    "EmailReplacer",
    "TransactionManager",
    "BatchProcessor",
]
