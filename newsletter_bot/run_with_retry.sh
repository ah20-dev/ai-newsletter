#!/bin/bash
set -e
cd /home/ssm-user/newsletter_bot
source venv/bin/activate

# Retry delays in seconds: 3min, 7min, 12min, 15min
DELAYS=(180 420 720 900)
MAX_ATTEMPTS=5  # 1 initial + 4 retries

attempt=1

run_bot() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | Attempt $attempt of $MAX_ATTEMPTS..."
  python main.py
}

until run_bot; do
  EXIT_CODE=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | Attempt $attempt failed (exit $EXIT_CODE)."

  if [ $attempt -ge $MAX_ATTEMPTS ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | All $MAX_ATTEMPTS attempts exhausted. Giving up."
    exit 1
  fi

  DELAY=${DELAYS[$attempt - 1]}
  DELAY_MIN=$(echo "scale=1; $DELAY / 60" | bc)
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | Retrying in ${DELAY_MIN} minutes (${DELAY}s)..."
  sleep $DELAY

  attempt=$((attempt + 1))
done

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | Success on attempt $attempt."
exit 0