from flask import Flask, jsonify, request
app = Flask(__name__)
import psycopg2
import pika
import requests
import shutil
import sys
from PIL import Image
from io import BytesIO

# Max thumb size
MAX_THUMB_SIZE = (320, 320)

# Initialize database connection
conn = psycopg2.connect("dbname=waldo user=waldo password=1234 host=postgres")

# Initliaze RabbitMQ connection
credentials = pika.PlainCredentials('rabbitmq', '1234')
connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq', 5672, '/', credentials))
channel = connection.channel()

@app.route("/")
def index():
    return jsonify(success=True)

@app.route("/photos/pending", methods=['GET'])
def fetch_pending_photos():
    results = []
    cur = conn.cursor()
    columns = []
    cur.execute("SELECT * from photos where status = 'pending'")
    conn.commit()
    for desc in cur.description:
        columns.append(desc[0])
    for row in cur.fetchall():
        results.append(dict(zip(columns, row)))
    return jsonify(results)

@app.route("/photos/process", methods=['POST'])
def process_pending_photos():
    photo_ids = request.get_json()
    # Iterate through photo_ids in POST
    for photo_id in photo_ids:
        cur = conn.cursor()
        query = f"UPDATE photos set status = 'processing' where uuid = '{photo_id}'"
        # Publish uuid to RabbitMQ
        channel.basic_publish(exchange='', routing_key='photo-processor', body=photo_id)
        # Execute postgres query
        cur.execute(query)
        conn.commit()
        cur = conn.cursor()
        query = f"SELECT url from photos where uuid = '{photo_id}'"
        print(query, file=sys.stderr)
        cur.execute(query)
        result = cur.fetchone()
        # Image url present in photos table
        if result is not None:
            url = result[0]
            # Download file
            print(url, file=sys.stderr)
            r = requests.get(url, stream=True, headers={ 'User-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.86 Safari/537.36' })
            print(f"STATUS CODE: {r.status_code}", file=sys.stderr)
            # Image fetched successfully from S3
            if r.status_code == 200:
                try:
                    # Create image object from downloaded image file
                    image = Image.open(BytesIO(r.content))
                    print(f"WIDTH: {image.width}, HEIGHT: {image.height}", file=sys.stderr)
                    # Create image thumb
                    image.thumbnail(MAX_THUMB_SIZE, Image.ANTIALIAS)
                    image.save(f'/waldo-app-thumbs/{photo_id}.jpg')
                    # Insert details into photo_thumbnails
                    cur = conn.cursor()
                    query = f"INSERT into photo_thumbnails (photo_uuid, width, height, url) values ('{photo_id}', {image.width}, {image.height}, '/waldo-app-thumbs/{photo_id}.jpg')"
                    cur.execute(query)
                    conn.commit()
                    # Mark photos status to completed
                    cur = conn.cursor()
                    query = f"UPDATE photos set status = 'completed' where uuid = '{photo_id}'"
                    cur.execute(query)
                    conn.commit()
                except Exception as e:
                    # On error, mark photos status to failed
                    print(e, file=sys.stderr)
                    cur = conn.cursor()
                    query = f"UPDATE photos set status = 'failed' where uuid = '{photo_id}'"
                    cur.execute(query)
                    conn.commit()
            # Image can't be fetched from S3, try next image
            else:
                continue
        # Image url not present in photos table
        else:
            pass
    # Return success at least one image is downloaded and processed
    return jsonify(success=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000)
