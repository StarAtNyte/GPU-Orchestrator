// SDXL Image Generator - Frontend JavaScript

class SDXLGenerator {
    constructor() {
        this.currentJobId = null;
        this.pollInterval = null;
        this.history = this.loadHistory();

        this.initElements();
        this.attachEventListeners();
        this.updateSliderValues();
        this.checkOrchestratorStatus();
        this.renderHistory();

        // Poll status every 5 seconds
        setInterval(() => this.checkOrchestratorStatus(), 5000);
    }

    initElements() {
        this.form = document.getElementById('generate-form');
        this.generateBtn = document.getElementById('generate-btn');

        this.stepsSlider = document.getElementById('steps');
        this.stepsValue = document.getElementById('steps-value');
        this.guidanceSlider = document.getElementById('guidance');
        this.guidanceValue = document.getElementById('guidance-value');

        this.emptyState = document.getElementById('empty-state');
        this.jobStatus = document.getElementById('job-status');
        this.imageResult = document.getElementById('image-result');
        this.errorState = document.getElementById('error-state');

        this.statusText = document.getElementById('status-text');
        this.progressFill = document.getElementById('progress-fill');
        this.currentJobIdEl = document.getElementById('current-job-id');
        this.resultImage = document.getElementById('result-image');
        this.jobMetadata = document.getElementById('job-metadata');
        this.errorMessage = document.getElementById('error-message');

        this.orchestratorStatus = document.getElementById('orchestrator-status');
        this.workersCount = document.getElementById('workers-count').querySelector('.count');

        this.historyGrid = document.getElementById('history-grid');
    }

    attachEventListeners() {
        this.form.addEventListener('submit', (e) => this.handleGenerate(e));
        this.stepsSlider.addEventListener('input', () => this.updateSliderValues());
        this.guidanceSlider.addEventListener('input', () => this.updateSliderValues());

        document.getElementById('download-btn')?.addEventListener('click', () => this.downloadImage());
        document.getElementById('new-generation-btn')?.addEventListener('click', () => this.resetForm());
        document.getElementById('retry-btn')?.addEventListener('click', () => this.resetForm());
    }

    updateSliderValues() {
        this.stepsValue.textContent = this.stepsSlider.value;
        this.guidanceValue.textContent = this.guidanceSlider.value;
    }

    async checkOrchestratorStatus() {
        try {
            const response = await fetch('/health');
            const data = await response.json();

            if (data.orchestrator === 'connected') {
                this.orchestratorStatus.classList.add('connected');
                this.orchestratorStatus.classList.remove('disconnected');
            } else {
                this.orchestratorStatus.classList.add('disconnected');
                this.orchestratorStatus.classList.remove('connected');
            }

            const workersResponse = await fetch('/api/workers');
            const workersData = await workersResponse.json();
            this.workersCount.textContent = workersData.count || 0;
        } catch (error) {
            this.orchestratorStatus.classList.add('disconnected');
            this.orchestratorStatus.classList.remove('connected');
            this.workersCount.textContent = '0';
        }
    }

    async handleGenerate(e) {
        e.preventDefault();

        const formData = new FormData(this.form);
        const data = {
            prompt: formData.get('prompt'),
            negative_prompt: formData.get('negative_prompt') || '',
            width: parseInt(formData.get('width')),
            height: parseInt(formData.get('height')),
            num_inference_steps: parseInt(formData.get('num_inference_steps')),
            guidance_scale: parseFloat(formData.get('guidance_scale')),
            seed: formData.get('seed') ? parseInt(formData.get('seed')) : null
        };

        this.setLoading(true);
        this.showJobStatus();
        this.statusText.textContent = 'Submitting job to orchestrator...';
        this.progressFill.style.width = '10%';

        try {
            const response = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                throw new Error('Failed to submit job');
            }

            const result = await response.json();

            if (result.success) {
                this.currentJobId = result.job_id;
                this.currentJobIdEl.textContent = result.job_id;
                this.statusText.textContent = 'Job queued, waiting for worker...';
                this.progressFill.style.width = '30%';
                this.startPolling(data);
            } else {
                throw new Error('Job submission failed');
            }
        } catch (error) {
            this.showError(error.message || 'Failed to connect to orchestrator');
            this.setLoading(false);
        }
    }

    startPolling(originalParams) {
        let pollCount = 0;
        const maxPolls = 120; // 2 minutes max

        this.pollInterval = setInterval(async () => {
            pollCount++;

            if (pollCount > maxPolls) {
                clearInterval(this.pollInterval);
                this.showError('Job timeout - took too long to complete');
                this.setLoading(false);
                return;
            }

            try {
                const response = await fetch(`/api/status/${this.currentJobId}`);

                if (!response.ok) {
                    if (response.status === 404) {
                        throw new Error('Job not found');
                    }
                    throw new Error('Failed to fetch status');
                }

                const data = await response.json();

                if (data.status === 'QUEUED') {
                    this.statusText.textContent = 'Waiting in queue...';
                    this.progressFill.style.width = '40%';
                } else if (data.status === 'PROCESSING') {
                    this.statusText.textContent = 'Generating image...';
                    this.progressFill.style.width = '70%';
                } else if (data.status === 'COMPLETED') {
                    clearInterval(this.pollInterval);
                    this.progressFill.style.width = '100%';
                    this.statusText.textContent = 'Complete!';

                    setTimeout(() => {
                        this.showResult(data, originalParams);
                        this.setLoading(false);
                    }, 500);
                } else if (data.status === 'FAILED') {
                    clearInterval(this.pollInterval);
                    this.showError(data.error || 'Job failed');
                    this.setLoading(false);
                }
            } catch (error) {
                clearInterval(this.pollInterval);
                this.showError(error.message);
                this.setLoading(false);
            }
        }, 1000);
    }

    showJobStatus() {
        this.emptyState.style.display = 'none';
        this.jobStatus.style.display = 'block';
        this.imageResult.style.display = 'none';
        this.errorState.style.display = 'none';
    }

    showResult(data, originalParams) {
        this.emptyState.style.display = 'none';
        this.jobStatus.style.display = 'none';
        this.imageResult.style.display = 'block';
        this.errorState.style.display = 'none';

        // Display the image
        if (data.result && data.result.image_base64) {
            this.resultImage.src = `data:image/png;base64,${data.result.image_base64}`;
        } else {
            this.showError('No image data received');
            return;
        }

        // Display metadata
        const metadata = `
            <p><strong>Job ID:</strong> ${data.job_id}</p>
            <p><strong>Prompt:</strong> ${originalParams.prompt}</p>
            <p><strong>Size:</strong> ${originalParams.width}x${originalParams.height}</p>
            <p><strong>Steps:</strong> ${originalParams.num_inference_steps}</p>
            <p><strong>Guidance:</strong> ${originalParams.guidance_scale}</p>
            ${data.result.seed ? `<p><strong>Seed:</strong> ${data.result.seed}</p>` : ''}
            ${data.result.time_taken ? `<p><strong>Time:</strong> ${data.result.time_taken.toFixed(2)}s</p>` : ''}
        `;
        this.jobMetadata.innerHTML = metadata;

        // Add to history
        this.addToHistory({
            jobId: data.job_id,
            image: this.resultImage.src,
            prompt: originalParams.prompt,
            timestamp: new Date().toISOString(),
            params: originalParams
        });
    }

    showError(message) {
        this.emptyState.style.display = 'none';
        this.jobStatus.style.display = 'none';
        this.imageResult.style.display = 'none';
        this.errorState.style.display = 'block';

        this.errorMessage.textContent = message;
    }

    setLoading(isLoading) {
        if (isLoading) {
            this.generateBtn.classList.add('loading');
            this.generateBtn.disabled = true;
        } else {
            this.generateBtn.classList.remove('loading');
            this.generateBtn.disabled = false;
        }
    }

    resetForm() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }

        this.emptyState.style.display = 'flex';
        this.jobStatus.style.display = 'none';
        this.imageResult.style.display = 'none';
        this.errorState.style.display = 'none';

        this.currentJobId = null;
        this.progressFill.style.width = '0%';
    }

    downloadImage() {
        const link = document.createElement('a');
        link.href = this.resultImage.src;
        link.download = `sdxl-${this.currentJobId || Date.now()}.png`;
        link.click();
    }

    addToHistory(item) {
        this.history.unshift(item);
        if (this.history.length > 20) {
            this.history = this.history.slice(0, 20);
        }
        this.saveHistory();
        this.renderHistory();
    }

    renderHistory() {
        if (this.history.length === 0) {
            this.historyGrid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1; text-align: center;">No generation history yet</p>';
            return;
        }

        this.historyGrid.innerHTML = this.history.map(item => `
            <div class="history-item" onclick="generator.viewHistoryItem('${item.jobId}')">
                <img src="${item.image}" alt="Generated image">
                <div class="history-item-info">
                    <p class="history-item-prompt">${item.prompt}</p>
                </div>
            </div>
        `).join('');
    }

    viewHistoryItem(jobId) {
        const item = this.history.find(h => h.jobId === jobId);
        if (!item) return;

        this.resultImage.src = item.image;
        this.currentJobId = item.jobId;

        const metadata = `
            <p><strong>Job ID:</strong> ${item.jobId}</p>
            <p><strong>Prompt:</strong> ${item.prompt}</p>
            <p><strong>Size:</strong> ${item.params.width}x${item.params.height}</p>
            <p><strong>Steps:</strong> ${item.params.num_inference_steps}</p>
            <p><strong>Guidance:</strong> ${item.params.guidance_scale}</p>
            <p><strong>Generated:</strong> ${new Date(item.timestamp).toLocaleString()}</p>
        `;
        this.jobMetadata.innerHTML = metadata;

        this.emptyState.style.display = 'none';
        this.jobStatus.style.display = 'none';
        this.imageResult.style.display = 'block';
        this.errorState.style.display = 'none';

        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    loadHistory() {
        try {
            const saved = localStorage.getItem('sdxl-history');
            return saved ? JSON.parse(saved) : [];
        } catch {
            return [];
        }
    }

    saveHistory() {
        try {
            localStorage.setItem('sdxl-history', JSON.stringify(this.history));
        } catch (error) {
            console.error('Failed to save history:', error);
        }
    }
}

// Initialize when DOM is ready
let generator;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        generator = new SDXLGenerator();
    });
} else {
    generator = new SDXLGenerator();
}
