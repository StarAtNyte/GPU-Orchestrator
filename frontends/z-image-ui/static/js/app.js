class ZImageUI {
    constructor() {
        this.currentJobId = null;
        this.pollInterval = null;
        this.zoomLevel = 1;
        this.isDragging = false;
        this.startX = 0;
        this.startY = 0;
        this.translateX = 0;
        this.translateY = 0;

        // Elements
        this.form = document.getElementById('generateForm');
        this.leftPanel = document.getElementById('leftPanel');

        // Right panel states
        this.placeholderState = document.getElementById('placeholderState');
        this.loadingState = document.getElementById('loadingState');
        this.imageResultState = document.getElementById('imageResultState');

        this.loadingText = document.getElementById('loadingText');
        this.jobIdDisplay = document.getElementById('jobIdDisplay');
        this.resultImage = document.getElementById('resultImage');
        this.generationInfo = document.getElementById('generationInfo');
        this.downloadBtn = document.getElementById('downloadBtn');
        this.newBtn = document.getElementById('newBtn');
        this.statusBadge = document.getElementById('statusBadge');
        this.statusText = document.getElementById('statusText');
        this.logoHome = document.getElementById('logoHome');

        // Sliders (optional - may not exist if advanced settings removed)
        this.stepsSlider = document.getElementById('steps');
        this.stepsValue = document.getElementById('stepsValue');
        this.shiftSlider = document.getElementById('shift');
        this.shiftValue = document.getElementById('shiftValue');
        this.randomSeedCheckbox = document.getElementById('randomSeed');
        this.seedGroup = document.getElementById('seedGroup');

        // Fullscreen
        this.fullscreenModal = document.getElementById('fullscreenModal');
        this.fullscreenImg = document.getElementById('fullscreenImg');
        this.fullscreenClose = document.getElementById('fullscreenClose');
        this.zoomInBtn = document.getElementById('zoomInBtn');
        this.zoomOutBtn = document.getElementById('zoomOutBtn');
        this.resetZoomBtn = document.getElementById('resetZoomBtn');

        // Event listeners
        this.form.addEventListener('submit', (e) => this.handleSubmit(e));
        this.newBtn.addEventListener('click', () => this.reset());
        this.logoHome.addEventListener('click', () => this.reset());
        this.downloadBtn.addEventListener('click', () => this.downloadImage());

        // Slider updates (only if elements exist)
        if (this.stepsSlider && this.stepsValue) {
            this.stepsSlider.addEventListener('input', (e) => {
                this.stepsValue.textContent = e.target.value;
            });
        }
        if (this.shiftSlider && this.shiftValue) {
            this.shiftSlider.addEventListener('input', (e) => {
                this.shiftValue.textContent = parseFloat(e.target.value).toFixed(1);
            });
        }

        // Random seed toggle (only if elements exist)
        if (this.randomSeedCheckbox && this.seedGroup) {
            this.randomSeedCheckbox.addEventListener('change', (e) => {
                this.seedGroup.style.display = e.target.checked ? 'none' : 'block';
            });
        }

        // Advanced settings toggle (only if elements exist)
        this.advancedToggle = document.getElementById('advancedToggle');
        this.advancedSettings = document.getElementById('advancedSettings');
        this.advancedIcon = document.getElementById('advancedIcon');
        this.currentSteps = document.getElementById('currentSteps');
        this.currentShift = document.getElementById('currentShift');

        if (this.advancedToggle && this.advancedSettings && this.advancedIcon) {
            this.advancedToggle.addEventListener('click', () => {
                const isHidden = this.advancedSettings.style.display === 'none';
                this.advancedSettings.style.display = isHidden ? 'block' : 'none';
                this.advancedIcon.style.transform = isHidden ? 'rotate(90deg)' : 'rotate(0deg)';
            });
        }

        // Update current values in toggle bar (only if elements exist)
        if (this.stepsSlider && this.currentSteps) {
            this.stepsSlider.addEventListener('input', (e) => {
                this.currentSteps.textContent = e.target.value;
            });
        }
        if (this.shiftSlider && this.currentShift) {
            this.shiftSlider.addEventListener('input', (e) => {
                this.currentShift.textContent = parseFloat(e.target.value).toFixed(1);
            });
        }

        // Example prompts
        const exampleBtns = document.querySelectorAll('.example-btn');
        exampleBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const prompt = btn.getAttribute('data-prompt');
                document.getElementById('prompt').value = prompt;

                // Visual feedback - reset all buttons
                exampleBtns.forEach(b => {
                    b.style.background = 'white';
                    b.style.color = 'var(--gray-700)';
                    b.style.borderColor = 'var(--gray-200)';
                    // Reset number badge color
                    const numberBadge = b.querySelector('.example-number');
                    if (numberBadge) {
                        numberBadge.style.background = 'var(--primary)';
                        numberBadge.style.color = 'white';
                    }
                });

                // Highlight selected button - use light blue background with dark text
                btn.style.background = '#e0f2fe';
                btn.style.color = 'var(--gray-700)';
                btn.style.borderColor = 'var(--primary)';

                // Keep number badge as is for selected
                const selectedNumberBadge = btn.querySelector('.example-number');
                if (selectedNumberBadge) {
                    selectedNumberBadge.style.background = 'var(--primary)';
                    selectedNumberBadge.style.color = 'white';
                }

                // Scroll to prompt
                document.getElementById('prompt').focus();
            });
        });

        // Fullscreen
        this.resultImage.addEventListener('click', () => this.openFullscreen());
        this.fullscreenClose.addEventListener('click', () => this.closeFullscreen());
        this.fullscreenModal.addEventListener('click', (e) => {
            if (e.target === this.fullscreenModal) this.closeFullscreen();
        });
        this.zoomInBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.zoomLevel = Math.min(this.zoomLevel + 0.5, 5);
            this.updateTransform();
        });
        this.zoomOutBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.zoomLevel = Math.max(this.zoomLevel - 0.5, 0.5);
            this.updateTransform();
        });
        this.resetZoomBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.zoomLevel = 1;
            this.translateX = 0;
            this.translateY = 0;
            this.updateTransform();
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (!this.fullscreenModal.classList.contains('active')) return;

            if (e.key === 'Escape') this.closeFullscreen();
            else if (e.key === '+' || e.key === '=') {
                this.zoomLevel = Math.min(this.zoomLevel + 0.5, 5);
                this.updateTransform();
            }
            else if (e.key === '-' || e.key === '_') {
                this.zoomLevel = Math.max(this.zoomLevel - 0.5, 0.5);
                this.updateTransform();
            }
            else if (e.key === '0') {
                this.zoomLevel = 1;
                this.translateX = 0;
                this.translateY = 0;
                this.updateTransform();
            }
        });

        // Mouse drag to pan
        this.fullscreenImg.addEventListener('mousedown', (e) => {
            if (this.zoomLevel > 1) {
                this.isDragging = true;
                this.startX = e.clientX - this.translateX;
                this.startY = e.clientY - this.translateY;
                this.fullscreenImg.style.cursor = 'grabbing';
            }
        });

        document.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.translateX = e.clientX - this.startX;
                this.translateY = e.clientY - this.startY;
                this.updateTransform();
            }
        });

        document.addEventListener('mouseup', () => {
            this.isDragging = false;
            if (this.zoomLevel > 1) {
                this.fullscreenImg.style.cursor = 'move';
            } else {
                this.fullscreenImg.style.cursor = 'zoom-out';
            }
        });

        // Mouse wheel zoom
        this.fullscreenImg.addEventListener('wheel', (e) => {
            e.preventDefault();
            if (e.deltaY < 0) {
                this.zoomLevel = Math.min(this.zoomLevel + 0.2, 5);
            } else {
                this.zoomLevel = Math.max(this.zoomLevel - 0.2, 0.5);
            }
            this.updateTransform();
        });

        // Check health
        this.checkHealth();
        setInterval(() => this.checkHealth(), 5000);
    }

    async checkHealth() {
        try {
            const response = await fetch('/health');
            const data = await response.json();
            if (data.orchestrator === 'connected') {
                this.statusBadge.classList.remove('disconnected');
                this.statusText.textContent = 'Ready • Orchestrator Connected';
            } else {
                this.statusBadge.classList.add('disconnected');
                this.statusText.textContent = 'Disconnected • Check Orchestrator';
            }
        } catch (error) {
            this.statusBadge.classList.add('disconnected');
            this.statusText.textContent = 'Disconnected • Check Orchestrator';
        }
    }

    async handleSubmit(e) {
        e.preventDefault();

        const formData = new FormData(this.form);
        const data = {
            prompt: formData.get('prompt'),
            resolution: formData.get('resolution'),
            steps: parseInt(formData.get('steps') || '9'),
            shift: parseFloat(formData.get('shift') || '3.0'),
            random_seed: this.randomSeedCheckbox ? this.randomSeedCheckbox.checked : true,
            seed: parseInt(formData.get('seed') || '42')
        };

        // Show loading state
        this.placeholderState.style.display = 'none';
        this.imageResultState.style.display = 'none';
        this.loadingState.style.display = 'flex';
        this.loadingText.textContent = 'SUBMITTING JOB...';
        this.jobIdDisplay.textContent = '';

        try {
            const response = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (result.success) {
                this.currentJobId = result.job_id;
                this.currentSeed = result.seed;
                this.jobIdDisplay.textContent = `Job ID: ${result.job_id}`;
                this.loadingText.textContent = 'QUEUED...';
                this.startPolling();
            } else {
                alert('Failed to submit job: ' + (result.error || 'Unknown error'));
                this.reset();
            }
        } catch (error) {
            alert('Error: ' + error.message);
            this.reset();
        }
    }

    startPolling() {
        this.pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/status/${this.currentJobId}`);
                const data = await response.json();

                if (data.success) {
                    const status = data.status;

                    if (status === 'QUEUED') {
                        this.loadingText.textContent = 'WAITING IN QUEUE...';
                    } else if (status === 'PROCESSING') {
                        this.loadingText.textContent = 'GENERATING IMAGE...';
                    } else if (status === 'COMPLETED') {
                        clearInterval(this.pollInterval);
                        this.showResult(data);
                    } else if (status === 'FAILED') {
                        clearInterval(this.pollInterval);
                        alert('Job failed: ' + (data.error || 'Unknown error'));
                        this.reset();
                    }
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 1000);
    }

    showResult(data) {
        this.loadingState.style.display = 'none';
        this.imageResultState.style.display = 'flex';

        // Parse result - handle both object and string formats
        let imageData = null;
        if (data.result) {
            if (typeof data.result === 'object') {
                // Direct object from JSON
                imageData = data.result;
            } else if (typeof data.result === 'string') {
                try {
                    // Parse JSON string
                    imageData = JSON.parse(data.result);
                } catch (e) {
                    console.error('Failed to parse result:', e);
                }
            }
        }

        if (imageData && imageData.image_base64) {
            // Convert base64 to data URL
            const imageUrl = `data:image/png;base64,${imageData.image_base64}`;
            this.resultImage.src = imageUrl;

            // Show generation info
            const resolution = document.getElementById('resolution').value;
            const steps = imageData.steps || document.getElementById('steps').value;
            this.generationInfo.textContent = `${resolution} • ${steps} steps • Seed: ${imageData.seed || this.currentSeed}`;
        } else {
            alert('No image in result');
            this.reset();
        }
    }

    downloadImage() {
        const link = document.createElement('a');
        link.href = this.resultImage.src;
        link.download = `z-image_${this.currentJobId}_${Date.now()}.png`;
        link.click();
    }

    openFullscreen() {
        this.fullscreenImg.src = this.resultImage.src;
        this.fullscreenModal.classList.add('active');
        this.zoomLevel = 1;
        this.translateX = 0;
        this.translateY = 0;
        this.updateTransform();
    }

    closeFullscreen() {
        this.fullscreenModal.classList.remove('active');
        this.zoomLevel = 1;
        this.translateX = 0;
        this.translateY = 0;
    }

    updateTransform() {
        this.fullscreenImg.style.transform = `translate(${this.translateX}px, ${this.translateY}px) scale(${this.zoomLevel})`;
        this.fullscreenImg.style.cursor = this.zoomLevel > 1 ? 'move' : 'zoom-out';
    }

    reset() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }
        this.loadingState.style.display = 'none';
        this.imageResultState.style.display = 'none';
        this.placeholderState.style.display = 'flex';
        this.currentJobId = null;
    }
}

// Initialize
new ZImageUI();
