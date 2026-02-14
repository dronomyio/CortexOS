import requests
import time
import json
from datetime import datetime
import argparse
import sys

def check_session_status(session_id, base_url="http://127.0.0.1:8000"):
    """Check the status of a given session"""
    url = f"{base_url}/v1/web/browse/{session_id}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching status: {str(e)}")
        return None

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Poll session status every 20 seconds')
    parser.add_argument('session_id', help='The session ID to monitor')
    parser.add_argument('--url', default='http://127.0.0.1:8000', 
                        help='Base URL of the API (default: http://127.0.0.1:8000)')
    args = parser.parse_args()

    print(f"Starting status checker for session: {args.session_id}")
    print(f"Using base URL: {args.url}")
    print("Press Ctrl+C to stop...")
    print("-" * 50)

    try:
        while True:
            # Get current timestamp
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Check status
            status_data = check_session_status(args.session_id, args.url)
            
            if status_data:
                # Pretty print the status
                print(f"\n[{current_time}] Status Update:")
                print(f"Status: {status_data['status']}")
                print(f"Message: {status_data['message']}")
                print(f"Step Count: {status_data['metadata']['step_count']}")
                print(f"Processing Time: {status_data['metadata']['processing_time']:.2f} seconds")
                
                # Check if task is complete
                if status_data['status'] in ['DONE', 'FAILED']:
                    print("\nTask completed!")
                    print(f"Final Status: {status_data['status']}")
                    print(f"Final Message: {status_data['message']}")
                    break
            
            # Wait for 20 seconds before next check
            time.sleep(20)
            
    except KeyboardInterrupt:
        print("\nStatus checking stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()