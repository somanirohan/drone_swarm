from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
root = Path(__file__).resolve().parents[2]
log = sorted((root / 'results').glob('save-flight-*.npy'))[-1]
data = np.load(log)
fig, axs = plt.subplots(1, 2, figsize=(10, 4))
for i in range(data['states'].shape[0]):
    state = data['states'][i]
    axs[0].plot(state[0], state[1], label=f'Drone {i+1}')
    axs[1].plot(data['timestamps'][i], state[2], label=f'Drone {i+1}')
axs[0].set(xlabel='X (m)', ylabel='Y (m)', title='PID circular flight')
axs[0].axis('equal')
axs[1].set(xlabel='Time (s)', ylabel='Altitude (m)', title='Altitude during flight')
for ax in axs:
    ax.legend()
    ax.grid(alpha=.25)
fig.tight_layout()
fig.savefig(root / 'flight-results.png', dpi=160)
print('Saved flight-results.png; finite states:', np.isfinite(data['states']).all())
