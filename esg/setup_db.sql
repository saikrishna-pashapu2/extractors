-- ESG News Portal - Database Setup Script
-- Run this against the new RDS instance after creation
-- Database: esgarticles (postgres db, postgres user)

-- Table: esg_articles
CREATE TABLE IF NOT EXISTS esg_articles (
    id SERIAL PRIMARY KEY,
    title TEXT,
    published DATE,
    summary TEXT,
    link TEXT UNIQUE,
    source TEXT,
    matched_keywords TEXT,
    save_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table: publications
CREATE TABLE IF NOT EXISTS publications (
    id SERIAL PRIMARY KEY,
    image_url TEXT,
    title TEXT,
    summary TEXT,
    link TEXT UNIQUE,
    source TEXT,
    published TIMESTAMP
);

-- Table: events
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    event_name TEXT,
    event_id TEXT UNIQUE,
    event_url TEXT,
    start_date DATE,
    end_date DATE,
    start_time TIME,
    end_time TIME,
    timezone TEXT,
    image_url TEXT,
    ticket_price TEXT,
    tickets_url TEXT,
    venue_name TEXT,
    venue_address TEXT,
    organizer_name TEXT,
    organizer_url TEXT,
    summary TEXT,
    tags TEXT,
    source TEXT,
    month TEXT,
    event_data JSONB,
    detail_scrape_status TEXT,
    original_language TEXT,
    translation_status TEXT,
    translation_model TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
