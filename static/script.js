function app() {
    return {
        youtubeUrl: '',
        videoDetails: null,
        transcript: '',
        summarizedTranscript: '',  // Add this line
        summaries: null,
        videoId: null,
        error: '',
        successMessage: '',
        loadingTranscript: false,
        loadingSummary: false,
        hasTranscript: false,
        discordConfigured: false,
        scheduleTime: '',
        showScheduleModal: false,
        customTweetText: '',
        customTweetUrl: '',
        customTweetHashtags: '',
        // videoDetails: {},
        selectedPlatforms: {
            telegram: false,
            discord: false,
            linkedin: false,
            instagram: false,
            twitter: false
        },

        // auth state (populated from server or /me)
        currentUser: null,
        isAuthenticated: false,

        get hasSelectedPlatforms() {
            return Object.values(this.selectedPlatforms).some(Boolean);
        },

        // initialize auth state from server or window.INITIAL_USER
        initAuth() {
            try {
                if (!this.isAuthenticated) {
                    // gentle reminder - actions will work but saving/scheduling needs sign-in
                    this.successMessage = 'Note: you are not signed-in. Sign in to enable account features.';
                }
                if (window && window.INITIAL_USER) {
                    this.currentUser = window.INITIAL_USER;
                    this.isAuthenticated = !!this.currentUser;
                    return;
                }

                fetch('/me', { headers: { 'Content-Type': 'application/json' } })
                    .then(r => { if (!r.ok) throw r; return r.json(); })
                    .then(data => {
                        if (data && data.user) {
                            this.currentUser = data.user;
                            this.isAuthenticated = true;
                        }
                    })
                    .catch(() => {});
            } catch (e) {}
        },

        async getTranscript() {
            if (!this.youtubeUrl) {
                this.error = 'Please enter a YouTube URL';
                return;
            }

            this.loadingTranscript = true;
            this.error = '';
            this.successMessage = '';

            try {
                const response = await fetch('/get_transcript', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ youtube_url: this.youtubeUrl })
                });

                const data = await response.json();

                if (data.success) {
                    this.videoDetails = data.video_details;
                    this.transcript = data.transcript;
                    this.summarizedTranscript = data.summarized_transcript;  // Add this line
                    this.videoId = data.video_id;
                    this.videoDetails.video_id = data.video_id;
                    this.hasTranscript = true;
                    this.successMessage = 'Transcript generated successfully! Click "Get Summaries" to create social media posts.';
                    if (this.isAuthenticated) {
                        this.successMessage += ' Your transcripts will be saved to your account.';
                    }
                } else {
                    this.error = data.error || 'Failed to get transcript';
                }
            } catch (error) {
                this.error = 'Network error: ' + error.message;
            } finally {
                this.loadingTranscript = false;
            }
        },

        async getSummary() {
            if (!this.videoId) {
                this.error = 'Please get transcript first';
                return;
            }

            this.loadingSummary = true;
            this.error = '';

            try {
                const response = await fetch('/get_summary', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        video_id: this.videoId
                    })
                });

                const data = await response.json();

                if (data.success) {
                    this.summaries = data.summaries;
                    this.discordConfigured = data.discord_configured || false;
                    this.successMessage = 'Social media summaries generated successfully!';
                } else {
                    this.error = data.error || 'Failed to generate summaries';
                }
            } catch (error) {
                this.error = 'Network error: ' + error.message;
            } finally {
                this.loadingSummary = false;
            }
        },

        async postToSocial(platform) {
            if (!this.videoId) {
                this.error = 'No video data available';
                return;
            }

            try {
                const response = await fetch('/post_to_social', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        video_id: this.videoId,
                        platform: platform
                    })
                });

                const data = await response.json();

                if (data.success) {
                    if (platform === 'twitter' && data.twitter_url) {
                        // Open Twitter in new tab for manual posting
                        window.open(data.twitter_url, '_blank');
                        this.successMessage = 'Twitter share page opened! Complete your post in the new tab.';
                    } else {
                        this.successMessage = `Posted to ${platform} successfully!`;
                    }

                    if (data.discord_message) {
                        await this.copyToClipboard(data.discord_message);
                        this.successMessage += ' Discord message copied to clipboard!';
                    }
                } else {
                    this.error = data.error || `Failed to post to ${platform}`;
                }
            } catch (error) {
                this.error = 'Network error: ' + error.message;
            }
        },

        async schedulePost(platform) {
            if (!this.videoId) {
                this.error = 'No video data available';
                return;
            }

            if (!this.scheduleTime) {
                this.error = 'Please select a schedule time first';
                return;
            }

            if (!this.isAuthenticated) {
                this.error = 'Please sign in to schedule posts to your account';
                return;
            }

            try {
                console.log('Scheduling post with time:', this.scheduleTime);

                const response = await fetch('/schedule_post', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        video_id: this.videoId,
                        platform: platform,
                        schedule_time: this.scheduleTime
                    })
                });

                const data = await response.json();

                if (data.success) {
                    const scheduleTime = new Date(this.scheduleTime);
                    this.successMessage = `✅ Post scheduled for ${platform} at ${scheduleTime.toLocaleString()}!`;
                    console.log('Schedule response:', data);
                } else {
                    this.error = data.error || `Failed to schedule post for ${platform}`;
                    console.error('Schedule error:', data.error);
                }
            } catch (error) {
                this.error = 'Network error: ' + error.message;
                console.error('Network error:', error);
            }
        },

        async scheduleMultiplePosts() {
            if (!this.videoId) {
                this.error = 'No video data available';
                return;
            }

            if (!this.scheduleTime) {
                this.error = 'Please select a schedule time';
                return;
            }

            if (!this.isAuthenticated) {
                this.error = 'Please sign in to schedule posts to your account';
                return;
            }

            if (!this.hasSelectedPlatforms) {
                this.error = 'Please select at least one platform';
                return;
            }

            this.showScheduleModal = false;
            const scheduledPlatforms = [];
            const errors = [];

            for (const platform in this.selectedPlatforms) {
                if (this.selectedPlatforms[platform]) {
                    try {
                        console.log(`Scheduling ${platform} with time:`, this.scheduleTime);

                        const response = await fetch('/schedule_post', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({
                                video_id: this.videoId,
                                platform: platform,
                                schedule_time: this.scheduleTime
                            })
                        });

                        const data = await response.json();
                        if (data.success) {
                            scheduledPlatforms.push(platform);
                            console.log(`Scheduled ${platform}:`, data);
                        } else {
                            errors.push(`${platform}: ${data.error}`);
                            console.error(`Failed to schedule ${platform}:`, data.error);
                        }
                    } catch (error) {
                        errors.push(`${platform}: ${error.message}`);
                        console.error(`Failed to schedule ${platform}:`, error);
                    }
                }
            }

            if (scheduledPlatforms.length > 0) {
                const scheduleTime = new Date(this.scheduleTime);
                this.successMessage = `✅ Scheduled posts for ${scheduledPlatforms.join(', ')} at ${scheduleTime.toLocaleString()}!`;
                // Reset selections
                for (const platform in this.selectedPlatforms) {
                    this.selectedPlatforms[platform] = false;
                }

                if (errors.length > 0) {
                    this.successMessage += ` (Some failed: ${errors.join('; ')})`;
                }
            } else {
                this.error = 'Failed to schedule any posts: ' + errors.join('; ');
            }
        },

        async generateCustomTweet() {
            if (!this.customTweetText) {
                this.error = 'Please enter tweet text';
                return;
            }

            try {
                const response = await fetch('/custom_tweet', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        text: this.customTweetText,
                        url: this.customTweetUrl,
                        hashtags: this.customTweetHashtags
                    })
                });

                const data = await response.json();

                if (data.success) {
                    // Open Twitter share URL in new tab
                    window.open(data.twitter_url, '_blank');
                    this.successMessage = 'Twitter share page opened! Complete your post in the new tab.';

                    // Clear form
                    this.customTweetText = '';
                    this.customTweetUrl = '';
                    this.customTweetHashtags = '';
                } else {
                    this.error = data.error || 'Failed to generate Twitter share link';
                }
            } catch (error) {
                this.error = 'Network error: ' + error.message;
            }
        },

        async saveSummary() {
            if (!this.isAuthenticated) {
                this.error = 'Please sign in to save summaries to your account.';
                return;
            }
            if (!this.videoId) {
                this.error = 'No video selected to save.';
                return;
            }

            const textToSave = this.summarizedTranscript || (this.summaries && this.summaries.full) || '';
            if (!textToSave) {
                this.error = 'Nothing to save yet. Generate a summary first.';
                return;
            }

            try {
                const resp = await fetch('/save_summary', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_id: this.videoId, text: textToSave })
                });
                const data = await resp.json();
                if (data.success) {
                    this.successMessage = data.message || 'Saved!';
                } else {
                    this.error = data.error || 'Failed to save summary';
                }
            } catch (err) {
                this.error = 'Network error: ' + err.message;
            }
        },

        formatDuration(seconds) {
            if (!seconds) return 'Unknown';
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = seconds % 60;
            return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
        },

        formatViews(views) {
            if (!views) return 'Unknown';
            if (views >= 1000000) {
                return (views / 1000000).toFixed(1) + 'M';
            } else if (views >= 1000) {
                return (views / 1000).toFixed(1) + 'K';
            }
            return views.toString();
        },

        async copyToClipboard(text) {
            try {
                await navigator.clipboard.writeText(text);
                this.successMessage = 'Copied to clipboard!';
                setTimeout(() => this.successMessage = '', 3000);
            } catch (error) {
                this.error = 'Failed to copy to clipboard';
            }
        }
    }

}
