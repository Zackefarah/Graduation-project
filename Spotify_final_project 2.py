from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.bash import BashOperator

import os
import requests
from datetime import datetime
import json
import pandas as pd
import configparser

import psycopg2 as ps 
from sqlalchemy import create_engine  

import matplotlib.pyplot as oPlot

from datetime import datetime, timedelta

import spotipy
from spotipy.oauth2 import SpotifyOAuth
import logging

import numpy as np

## Folders and targets
AIRFLOW_HOME = os.environ['AIRFLOW_HOME']
config = configparser.ConfigParser()
config.read(AIRFLOW_HOME + '/airflow.cfg')
AIRFLOW_DAGS = config.get('core', 'dags_folder')
CURR_DIR_PATH = os.path.dirname(os.path.realpath(__file__))


# Spotify API credentials # Later we can create a config file to hide the below credentials
client_id = '7ac4100bc0f84e978f1b4c8e4b74576b'
client_secret = '62fed3a94a224ef48272a7b3d8ea0583'
redirect_uri = 'http://localhost:8888/callback'
scope = 'user-read-recently-played'


## Function to download the json data from API and save the data in json format to SPOTIFY_JSON_TARGET
def _download_from_spotify_api():
    # Set up logging
    logging.basicConfig(level=logging.INFO)

    # Initialize Spotipy with Spotify credentials
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=client_id,
                                                client_secret=client_secret,
                                                redirect_uri=redirect_uri,
                                                scope=scope))
    
    # Calculate yesterday's midnight in Unix timestamp (milliseconds) 
    yesterday_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1) 
    yesterday_midnight_unix = int(yesterday_midnight.timestamp() * 1000) 
    logging.info(f"Yesterday's midnight in Unix timestamp (milliseconds): {yesterday_midnight_unix}")
    
    # Fetch recently played tracks
    results = sp.current_user_recently_played(limit=50)

    # Save raw JSON data
    with open('recently_played_tracks_raw.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    logging.info("Raw data saved to recently_played_tracks_raw.json")
    
    # Convert JSON to DataFrame and save as CSV
    df = pd.json_normalize(results['items'])  # Flatten the JSON structure to create a table
    df.to_csv('recently_played_tracks_raw.csv', index=False)
    logging.info("Raw data saved to recently_played_tracks_raw.csv")


## Prepare the data and save it in csv format to SPOTIFY_PREPARED_CSV
def prepare_listening_date_dataframe():
    # Load the CSV into a DataFrame
    df = pd.read_csv('recently_played_tracks_raw.csv')

    # Ensure the 'played_at' column is in datetime format
    df['date_time'] = pd.to_datetime(df['played_at'])

    # Extract hour, weekday, week of year, month number, month name, quarter, and year
    df['hour'] = df['date_time'].dt.hour
    df['weekday'] = df['date_time'].dt.day_name()
    df['week_of_year'] = df['date_time'].dt.isocalendar().week
    df['month_num'] = df['date_time'].dt.month
    df['month_name'] = df['date_time'].dt.month_name()
    df['quarter'] = df['date_time'].dt.quarter
    df['year'] = df['date_time'].dt.year

    # Determine if the day is a weekend
    df['weekend'] = np.where(df['weekday'].isin(['Saturday', 'Sunday']), 'Yes', 'No')

    # Select only the columns relevant for the listening_date table
    listening_date_df = df[['date_time', 'hour', 'weekday', 'week_of_year', 'month_num', 'month_name', 'quarter', 'year', 'weekend']]

    return listening_date_df


def _prepare_data():
    df = pd.read_csv('recently_played_tracks_raw.csv')
    
    columns_to_drop = [
        'context', 
        'track.album.artists', 
        'track.album.available_markets', 
        'track.album.uri',
        'track.album.href', 
        'track.album.id', 
        'track.album.images', 
        'track.album.type', 
        'track.album.release_date_precision',
        'track.available_markets', 
        'track.disc_number', 
        'track.explicit', 
        'track.external_ids.isrc', 
        'track.external_urls.spotify',
        'track.href', 
        'track.is_local', 
        'track.preview_url', 
        'track.track_number', 
        'track.type', 
        'track.uri'
    ]

    df.drop(columns=columns_to_drop, inplace=True)

    df['track.duration_ms'] = df['track.duration_ms'] / 1000
    df.rename(columns={'track.duration_ms': 'track.duration_sec'}, inplace=True)

    def parse_artists(artists_str):
        try:
            # Parse the JSON-like string into a Python list
            artists = json.loads(artists_str.replace("'", '"'))
            # Extract artist names
            artist_names = [artist['name'] for artist in artists]
            return artist_names
        except (json.JSONDecodeError, TypeError):
            # Handle any errors that occur during parsing
            return []

    # Apply the parsing function to the 'track.artists' column
    df['artist_names'] = df['track.artists'].apply(parse_artists)

    listening_date_dataframe = prepare_listening_date_dataframe()

    # played_at column no longer needed after its extracted to other dataframe
    df.drop(columns=['played_at'], inplace=True)

    df.to_csv('cleaned_data.csv', index=False)
    listening_date_dataframe.to_csv('listening_date_data.csv', index=False)

_download_from_spotify_api()
_prepare_data()

## STAGE 

spotify_prepared_data_path = SPOTIFY_PREPARED_CSV

def execute_sql_commands():
    # Connect to the database using psycopg2
    conn = ps.connect(dbname="tuomas", user="tuomas")
    cur = conn.cursor()

    # List of SQL commands to execute
    sql_commands = [
        """
        CREATE TABLE IF NOT EXISTS public.tracks (
            track_id serial NOT NULL,
            track_name text,
            track_lenghts integer,
            date_id integer,
            album_id integer,
            popularity_id integer,
            PRIMARY KEY (track_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS public.album (
            album_id serial NOT NULL,
            album_name text,
            album_link text,
            album_type text,
            total_tracks integer,
            release_year integer,
            PRIMARY KEY (album_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS public.listening_date (
            date_id serial NOT NULL,
            date_time timestamp without time zone,
            hour integer,
            weekday text,
            week_of_year integer,
            month_num integer,
            month_name text,
            quarter integer,
            year integer,
            weekend text,
            PRIMARY KEY (date_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS public.artists (
            artist_id serial NOT NULL,
            artist text,
            PRIMARY KEY (artist_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS public.popularity (
            popularity_id serial NOT NULL,
            popularity integer,
            PRIMARY KEY (popularity_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS public.artists_tracks (
            track_id integer,
            artist_id integer,
            id serial NOT NULL,
            PRIMARY KEY (id)
        );
        """,
        """
        ALTER TABLE IF EXISTS public.tracks
            ADD CONSTRAINT tracks_listening_date FOREIGN KEY (date_id)
            REFERENCES public.listening_date (date_id) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE NO ACTION
            NOT VALID;
        """,
        """
        ALTER TABLE IF EXISTS public.tracks
            ADD CONSTRAINT tracks_album FOREIGN KEY (album_id)
            REFERENCES public.album (album_id) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE NO ACTION
            NOT VALID;
        """,
        """
        ALTER TABLE IF EXISTS public.tracks
            ADD CONSTRAINT tracks_popularity FOREIGN KEY (popularity_id)
            REFERENCES public.popularity (popularity_id) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE NO ACTION
            NOT VALID;
        """,
        """
        ALTER TABLE IF EXISTS public.artists_tracks
            ADD CONSTRAINT arttrack_tracks FOREIGN KEY (track_id)
            REFERENCES public.tracks (track_id) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE NO ACTION
            NOT VALID;
        """,
        """
        ALTER TABLE IF EXISTS public.artists_tracks
            ADD CONSTRAINT arttrack_artists FOREIGN KEY (artist_id)
            REFERENCES public.artists (artist_id) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE NO ACTION
            NOT VALID;
        """
    ]

    # Execute each SQL command
    for command in sql_commands:
        cur.execute(command)

    # Commit the changes to the database
    conn.commit()

    # Close the cursor and the connection
    cur.close()
    conn.close()



def _stage():
    execute_sql_commands()
    
    spotify_data = pd.read_csv(
    spotify_prepared_data_path,
    sep=",",
    # header=None
    # encoding="unicode_escape"
    )

    # fake_data.columns = ["name", "age"] # Change column names if needed
    conn = ps.connect(dbname="tuomas", user="tuomas")

    cur = conn.cursor()

    with postgres_engine.connect() as conn:
        # Drop the table with CASCADE if it exists
        conn.execute("DROP TABLE IF EXISTS spotify_data CASCADE;") ## Need to be made for the spotify database views


    # Write to new sources
    spotify_data.to_sql(name="spotify_data", con=postgres_engine, if_exists="replace", index=False) # write to postgres


## MODEL ##

def _model():
    # Change dbname to be the name of your schema and user to be the owner of said database schema
    conn = ps.connect(dbname="tuomas", user="tuomas")

    cur = conn.cursor()

    cur.execute("CREATE VIEW norway_tomorrow AS SELECT temperature FROM norway_data WHERE date::date = CURRENT_DATE + INTERVAL '1 day';")

    cur.execute("CREATE VIEW swe_tomorrow AS SELECT temperature FROM swe_data WHERE date::date = CURRENT_DATE + INTERVAL '1 day';")

    cur.execute("SELECT * FROM norway_tomorrow;") 
    result_norway = cur.fetchall()

    conn.commit()
    

    cur.execute("SELECT * FROM swe_tomorrow;")
    result_swe = cur.fetchall()

    conn.commit()

    cur.close()
    conn.close()

    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    hours_24 = list(range(1, 25))
    oPlot.figure(facecolor="grey")
    oPlot.plot(hours_24, result_swe, color="black")
    oPlot.scatter(hours_24, result_swe, color="red")
    oPlot.title("Tomorrow's weather in Östhammar, Sweden", color="black", fontweight="bold")
    oPlot.xlabel("Hours", color="black")
    oPlot.ylabel("Temperature (°C)", color = "black")
    oPlot.xticks(list(range(1, 25)), labels=hours_24, rotation=30)
    oPlot.savefig(f'weather_tomorrow_swe_{tomorrow_date}.png')
    
    oPlot.figure(facecolor="grey")
    oPlot.plot(hours_24, result_norway, color="black")
    oPlot.scatter(hours_24, result_norway, color="red")
    oPlot.title("Tomorrow's weather in Oslo Opera house, Norway", color="black", fontweight="bold")
    oPlot.xlabel("Hours", color="black")
    oPlot.ylabel("Temperature (°C)", color = "black")
    oPlot.xticks(list(range(1, 25)), labels=hours_24, rotation=30)
    oPlot.savefig(f'weather_tomorrow_nor_{tomorrow_date}.png')


## DAG ##
with DAG(
    "mini_project_dag",
    start_date=datetime(2021, 1, 1), 
    schedule="@DAILY", # Hourly
    catchup=False
):
    download = PythonOperator(
        task_id="download",
        python_callable=_download_from_spotify_api
    )

    prepare_data = PythonOperator(
        task_id="prepare_data",
        python_callable=_prepare_data
    )

    stage = PythonOperator(
        task_id="stage",
        python_callable=_stage
    )

    model = PythonOperator(
        task_id="model",
        python_callable=_model
    )
    
    download >> prepare_data >> stage >> model
