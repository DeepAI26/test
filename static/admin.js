function adminApp() {
    return {
        // State
        scheduledPosts: [],
        videoData: {},
        systemStatus: {
            scheduler_alive: false,
            video_count: 0,
            post_status_counts: {},
            current_time_utc: null
        },
        loading: false,
        showDetailsModal: false,
        showVideoModal: false,
        showRawModal: false,
        selectedPost: null,
        selectedVideoId: null,
        selectedVideoData: null,
        videoIdSearch: '',
        rawJsonText: '',
        error: '',
        successMessage: '',

        // Lifecycle helpers
        async refreshAll() {
            this.loading = true;
            this.error = '';
            try {
                await Promise.all([this.loadSystemStatus(), this.loadScheduledPosts(), this.loadVideoData()]);
                this.successMessage = 'Refreshed dashboard';
                setTimeout(() => (this.successMessage = ''), 2000);
            } catch (e) {
                this.error = 'Failed to refresh dashboard: ' + (e.message || e);
            } finally {
                this.loading = false;
            }
        },
        // --- utility helpers used by template ---
        // Format ISO time into a readable string in a configured timezone (Toronto)
        formatDateTime(isoStr, timeZone = 'America/Toronto') {
            if (!isoStr) return 'Unknown';
            try {
                // If the string looks like a naive ISO (no timezone) we assume UTC and append Z
                // Example naive formats: 2024-01-15T14:30 or 2024-01-15T14:30:00
                if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(isoStr)) {
                    isoStr = isoStr + 'Z';
                }

                const dt = new Date(isoStr);
                if (isNaN(dt)) return isoStr;

                // Use Intl.DateTimeFormat with the requested timezone for consistent display
                const opts = {
                    timeZone,
                    year: 'numeric', month: 'short', day: 'numeric',
                    hour: 'numeric', minute: '2-digit', second: undefined,
                };
                return new Intl.DateTimeFormat(undefined, opts).format(dt);
            } catch (e) {
                return isoStr;
            }
        },

        // --- admin API actions ---
        async loadScheduledPosts() {
            try {
                const res = await fetch('/admin/api/scheduled_posts');
                const data = await res.json();
                if (data.success) {
                    this.scheduledPosts = data.posts || [];
                } else {
                    this.error = data.error || 'Failed to load scheduled posts';
                }
            } catch (e) {
                this.error = 'Network error loading scheduled posts: ' + (e.message || e);
            }
        },

        async loadVideoData() {
            try {
                const res = await fetch('/admin/api/video_data');
                const data = await res.json();
                if (data.success) {
                    this.videoData = data.video_data || {};
                } else {
                    this.error = data.error || 'Failed to load video data';
                }
            } catch (e) {
                this.error = 'Network error loading video data: ' + (e.message || e);
            }
        },

        async loadSystemStatus() {
            try {
                const res = await fetch('/admin/api/system_status');
                const data = await res.json();
                if (data.success) {
                    this.systemStatus = data.system_status || this.systemStatus;
                } else {
                    this.error = data.error || 'Failed to load system status';
                }
            } catch (e) {
                this.error = 'Network error loading system status: ' + (e.message || e);
            }
        },

        // run one scheduled post right now
        async runPostNow(postId) {
            this.loading = true;
            try {
                const res = await fetch(`/admin/api/run_post_now/${postId}`, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    this.successMessage = data.message || 'Post executed';
                } else {
                    this.error = data.error || 'Failed to run post';
                }
            } catch (e) {
                this.error = 'Network error: ' + (e.message || e);
            } finally {
                this.loading = false;
                await this.refreshAll();
            }
        },

        // view a specific video JSON/details via id
        viewVideoJson(videoId) {
            this.selectedVideoId = videoId;
            this.selectedVideoData = this.videoData[videoId] || null;
            if (!this.selectedVideoData) {
                this.error = `Video id ${videoId} not found.`;
                this.showVideoModal = false;
                return;
            }
            this.showVideoModal = true;
        },

        // lookup by id from the search input
        lookupVideoById() {
            const id = (this.videoIdSearch || '').trim();
            if (!id) {
                this.error = 'Please enter a video id to search';
                return;
            }
            this.error = '';
            if (this.videoData[id]) {
                this.viewVideoJson(id);
            } else {
                this.error = `Video id ${id} not found in video data.`;
            }
        },

        // show raw JSON for system_status, scheduled_posts, video_data
        viewRaw(kind) {
            try {
                let obj;
                switch (kind) {
                    case 'system_status':
                        obj = this.systemStatus; break;
                    case 'scheduled_posts':
                        obj = this.scheduledPosts; break;
                    case 'video_data':
                        obj = this.videoData; break;
                    default:
                        obj = { error: 'unknown kind' };
                }
                this.rawJsonText = JSON.stringify(obj, null, 2);
                this.showRawModal = true;
            } catch (e) {
                this.error = 'Failed to build raw JSON: ' + (e.message || e);
            }
        },

        async deletePost(postId) {
            if (!confirm('Delete scheduled post #' + postId + '?')) return;
            this.loading = true;
            try {
                const res = await fetch(`/admin/api/delete_scheduled_post/${postId}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    this.successMessage = data.message || 'Post deleted';
                } else {
                    this.error = data.error || 'Failed to delete post';
                }
            } catch (e) {
                this.error = 'Network error: ' + (e.message || e);
            } finally {
                this.loading = false;
                await this.refreshAll();
            }
        },

        async deleteVideo(videoId) {
            if (!confirm('Delete video data ' + videoId + '?')) return;
            this.loading = true;
            try {
                const res = await fetch(`/admin/api/delete_video/${videoId}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    this.successMessage = data.message || 'Video deleted';
                } else {
                    this.error = data.error || 'Failed to delete video';
                }
            } catch (e) {
                this.error = 'Network error: ' + (e.message || e);
            } finally {
                this.loading = false;
                await this.refreshAll();
            }
        },

        formatDuration(seconds) {
            if (!seconds && seconds !== 0) return 'Unknown';
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = Math.floor(seconds % 60);
            return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
        },

        formatViews(views) {
            if (!views && views !== 0) return 'Unknown';
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
                setTimeout(() => (this.successMessage = ''), 2000);
            } catch (e) {
                this.error = 'Failed to copy to clipboard: ' + (e.message || e);
            }
        },

        // small helpers to style badges in template
        getStatusColor(status) {
            switch ((status || '').toLowerCase()) {
                case 'scheduled':
                    return 'bg-purple-100 text-purple-700';
                case 'posted':
                    return 'bg-green-100 text-green-700';
                case 'posting':
                    return 'bg-yellow-100 text-yellow-700';
                case 'failed':
                    return 'bg-red-100 text-red-700';
                default:
                    return 'bg-gray-100 text-gray-700';
            }
        },

        getStatusBadgeClass(status) {
            switch ((status || '').toLowerCase()) {
                case 'scheduled':
                    return 'bg-purple-100 text-purple-700';
                case 'posted':
                    return 'bg-green-100 text-green-700';
                case 'posting':
                    return 'bg-yellow-100 text-yellow-700';
                case 'failed':
                    return 'bg-red-100 text-red-700';
                default:
                    return 'bg-gray-100 text-gray-700';
            }
        },

        getPlatformBadgeClass(platform) {
            switch ((platform || '').toLowerCase()) {
                case 'telegram':
                    return 'bg-blue-100 text-blue-700';
                case 'discord':
                    return 'bg-indigo-100 text-indigo-700';
                case 'twitter':
                    return 'bg-sky-100 text-sky-700';
                case 'linkedin':
                    return 'bg-blue-50 text-blue-700';
                case 'instagram':
                    return 'bg-pink-50 text-pink-700';
                default:
                    return 'bg-gray-50 text-gray-700';
            }
        },

        showPostDetails(post) {
            this.selectedPost = post;
            this.showDetailsModal = true;
        },


        // initialize on load
        init() {
            this.refreshAll();
        }
    };
}
