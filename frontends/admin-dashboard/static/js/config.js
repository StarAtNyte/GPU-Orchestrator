/**
 * Configuration Viewer JavaScript
 * Displays system configuration (apps, workers, settings)
 */

/**
 * Load configuration data
 */
async function loadConfiguration() {
    try {
        const response = await fetch('/api/config');
        if (!response.ok) throw new Error('Failed to fetch configuration');

        const config = await response.json();
        displayAppsConfig(config.apps || {});
    } catch (error) {
        console.error('Error loading configuration:', error);
        showError('Failed to load configuration');
    }
}

/**
 * Display apps configuration
 */
function displayAppsConfig(apps) {
    const tbody = document.getElementById('apps-config-tbody');

    if (Object.keys(apps).length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="px-6 py-8 text-center text-gray-400">
                    No apps configured
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = Object.entries(apps).map(([appId, config]) => `
        <tr class="border-b border-[#363168]">
            <td class="px-6 py-4 font-medium text-white">${appId}</td>
            <td class="px-6 py-4">${config.name || 'N/A'}</td>
            <td class="px-6 py-4">${config.type || 'N/A'}</td>
            <td class="px-6 py-4">${config.queue || 'N/A'}</td>
            <td class="px-6 py-4">${config.gpu_vram_gb ? config.gpu_vram_gb + ' GB' : 'N/A'}</td>
            <td class="px-6 py-4">
                <code class="text-xs text-gray-400">${config.frontend_url || 'N/A'}</code>
            </td>
        </tr>
    `).join('');
}

/**
 * Initialize page
 */
document.addEventListener('DOMContentLoaded', () => {
    loadConfiguration();
});
