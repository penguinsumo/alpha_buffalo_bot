"""
alpha_buffalo_signal.py — Alpha Buffalo v11.2 (New V4 Hybrid)
- Uses  from signal_composer
- No‑None pipeline with DecisionValidator
"""
import os, logging, sys
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from scenario_scanner import scanner as scenario_scanner
from data_provider_twelvedata import fetch_twelvedata

