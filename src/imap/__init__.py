"""IMAP client module for Gmail access."""

from src.imap.client import GmailIMAPClient
from src.imap.scanner import EmailScanner
from src.imap.search import GmailSearcher, SearchCriteria

__all__ = ["GmailIMAPClient", "GmailSearcher", "SearchCriteria", "EmailScanner"]
