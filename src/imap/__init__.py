"""IMAP client module for Gmail access."""

from src.imap.client import GmailIMAPClient
from src.imap.search import GmailSearcher, SearchCriteria
from src.imap.scanner import EmailScanner

__all__ = ["GmailIMAPClient", "GmailSearcher", "SearchCriteria", "EmailScanner"]
