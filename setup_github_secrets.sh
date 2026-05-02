#!/bin/bash
# Run this after installing GitHub CLI: brew install gh (Mac) or https://cli.github.com
# Then: gh auth login
# Then: bash setup_github_secrets.sh

REPO="bjosh12/granada-hotel-price-scraper"

echo "Setting GitHub secrets for $REPO..."

gh secret set SUPABASE_URL --repo $REPO --body "https://oaohsencjgbzrdmlxrkg.supabase.co"
gh secret set SUPABASE_KEY --repo $REPO --body "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9hb2hzZW5jamdienJkbWx4cmtnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc3MzAzNjQsImV4cCI6MjA5MzMwNjM2NH0.8FG3K74yMHajGVn7jq4pUu5gQJoHjJ8moaM8yZZeCuo"
gh secret set RESEND_API_KEY --repo $REPO --body "re_a4hkL4vH_236vUwXzEDuKcvdsT4Bjtczd"
gh secret set FROM_EMAIL --repo $REPO --body "joshua@lingologic.es"
gh secret set EMAIL_1 --repo $REPO --body "joshua.aguirre.dst@gmail.com"
gh secret set ALERT_EMAIL --repo $REPO --body "joshua.aguirre.dst@gmail.com"

echo "All secrets set!"
