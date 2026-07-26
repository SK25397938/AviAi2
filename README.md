# ✈️ AviAi – Real-Time Aviation Intelligence Platform

## 📖 Overview

**AviAi** is a real-time aviation intelligence platform that ingests live global aircraft data, performs trajectory and behavioral analysis, and visualizes air traffic through an interactive flight radar interface.

Built using an asynchronous architecture, AviAi combines **FastAPI**, **Redis**, **WebSockets**, and **Leaflet** to deliver low-latency aircraft tracking and scalable aviation intelligence. The platform also features an experimental **Chart Intelligence Engine**, enabling users to upload aviation charts and query them using natural language.

Designed as a foundation for future **Air Traffic Control (ATC) decision support**, AviAi aims to bridge live surveillance, AI-powered analysis, and aviation document intelligence.

---

# ✨ Features

## ✈️ Live Aircraft Surveillance

- 🌍 Real-time aircraft data from OpenSky Network
- ⚡ Asynchronous data ingestion pipeline
- 📡 WebSocket-based live aircraft streaming
- 🚀 Redis-powered low-latency state caching
- 🗺️ Interactive flight-radar style map
- 🛫 Heading-rotated aircraft icons
- 📈 Altitude-colored trajectory trails
- 🎯 Aircraft selection and highlighting
- 📊 Live aircraft tooltips (Callsign, Altitude, Speed, Heading)

---

## 🧠 Aircraft Intelligence Engine

The intelligence engine continuously analyzes aircraft movement and generates operational insights.

Current capabilities include:

- 📈 Vertical trend detection (Climb, Cruise, Descent)
- ✈️ Aircraft behavior classification
- 🚨 Flight event detection
- 🛣️ Route inference from live trajectories

---

## 📄 Chart Intelligence (Experimental)

The Chart Intelligence module allows users to upload official aviation charts and interact with them using natural language.

### Current Capabilities

- 📤 Upload AIP and Airport Chart PDFs
- 📑 Multi-page text extraction
- 🔍 Semantic search over chart content
- 💾 Session-based document indexing
- 💬 Natural language question answering
- 📄 Source-grounded responses
- 🚫 Hallucination prevention using retrieved chart content

---

### Supported Queries

- Which airport chart is this?
- What runways are available?
- What is the aerodrome elevation?
- What magnetic variation is published?
- What operational remarks apply?
- What are the runway dimensions?

---

### Current Limitations

- Taxiway diagrams are not yet spatially parsed
- Instrument approach procedures are not decoded
- Frequency tables require structured extraction
- Visual chart highlighting is not yet supported

---

## 🖥️ High-Performance Frontend

- 🗺️ Leaflet interactive map
- ⚡ Smooth aircraft animation
- 📍 Live aircraft tooltips
- 🔄 Automatic WebSocket reconnection
- 📱 Responsive interface

---

## ⚙️ Scalable Backend

- FastAPI asynchronous server
- aiohttp ingestion client
- Redis state management
- Modular intelligence architecture
- WebSocket streaming pipeline

---

# 🛠️ Tech Stack

## Frontend

- HTML5
- CSS3
- JavaScript
- Leaflet.js

## Backend

- Python
- FastAPI
- Uvicorn
- aiohttp
- WebSockets

## Data & Storage

- Redis
- OpenSky Network API
- Airports CSV Database

## AI & Intelligence

- Semantic Retrieval
- Trajectory Analysis
- Event Detection
- Behavioral Classification
- Route Inference

---

# ⚙️ System Architecture

```text
OpenSky Network API
          │
          ▼
Async Ingestion Engine (aiohttp)
          │
          ▼
Redis Aircraft State Store
          │
          ▼
FastAPI Backend
          │
          ▼
WebSocket Flight Stream
          │
          ▼
Leaflet Live Map
```

---

# 📂 Project Structure

```text
AviAi/
│
├── app/
│   ├── api/
│   │   ├── flights.py
│   │   ├── ai.py
│   │   └── charts.py
│   │
│   ├── ingest/
│   │   ├── opensky_async.py
│   │   └── opensky_auth.py
│   │
│   ├── intelligence/
│   │   ├── trajectory.py
│   │   ├── behavior.py
│   │   ├── event_engine.py
│   │   └── route_engine.py
│   │
│   ├── core/
│   │   └── config.py
│   │
│   └── main.py
│
├── frontend/
│   ├── index.html
│   └── plane.svg
│
├── static/
│   └── charts.html
│
├── data/
│   └── airports.csv
│
├── requirements.txt
├── README.md
└── .gitignore
```

---

# ⚙️ How It Works

1. OpenSky Network provides live aircraft states.
2. The asynchronous ingestion engine continuously retrieves aircraft data.
3. Aircraft states are enriched using:
   - Vertical trend detection
   - Behavior classification
   - Event detection
   - Route inference
4. Aircraft information is cached in Redis.
5. FastAPI broadcasts updates through WebSockets.
6. Leaflet renders aircraft positions and trajectories in real time.

---

# 🚀 Installation

## 1. Clone the Repository

```bash
git clone https://github.com/SK25397938/AviAi.git

cd AviAi
```

---

## 2. Create a Virtual Environment

```bash
python -m venv venv
```

Windows

```bash
venv\Scripts\activate
```

Linux/macOS

```bash
source venv/bin/activate
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Configure Environment Variables

Create a `.env` file:

```env
OPENSKY_CLIENT_ID=your_client_id

OPENSKY_CLIENT_SECRET=your_client_secret

REDIS_HOST=localhost

REDIS_PORT=6379
```

---

## 5. Start Redis

Run Redis locally or via Docker.

---

## 6. Launch the Application

```bash
python -m uvicorn app.main:app --reload
```

---

# 🌐 Usage

### ✈️ Live Flight Map

```
http://127.0.0.1:8000/map/
```

### ❤️ Health Check

```
http://127.0.0.1:8000/health
```

### 📡 WebSocket Flight Stream

```
ws://127.0.0.1:8000/flights/ws
```

---

# 📊 Performance

- ⚡ ~0.5 second refresh interval
- 🌍 Supports thousands of simultaneous aircraft
- 🚀 Fully asynchronous ingestion
- 🎞️ Smooth rendering using `requestAnimationFrame`
- 🔄 Automatic WebSocket recovery

---

# 🚀 Future Enhancements

- 🤖 AI-powered Air Traffic Control Assistant
- 📄 Advanced aviation chart parser
- 🛬 Taxi routing intelligence
- 🛰️ Airport surface movement guidance
- 📈 Flight plan correlation
- ⚠️ Conflict detection and prediction
- 📊 Aviation analytics dashboard
- 🛫 Airport arrival and departure prediction

---


# ⭐ Support

If you found this project useful, consider giving it a **⭐ Star** on GitHub!
