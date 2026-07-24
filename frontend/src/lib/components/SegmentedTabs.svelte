<script lang="ts">
	// Shared segmented tab strip (#72 quality pass): one implementation for
	// the Timeline|Replay and Live|Replay-day toggles. Parent owns state and
	// reacts to `change`; labels resolve in the parent so i18n stays at the
	// call site.
	import { createEventDispatcher } from 'svelte';

	export let options: { id: string; label: string; testid?: string }[] = [];
	export let value: string;

	const dispatch = createEventDispatcher<{ change: { id: string } }>();
</script>

<div class="btn-group variant-soft" role="tablist">
	{#each options as opt (opt.id)}
		<button
			type="button"
			class="btn btn-sm {value === opt.id ? 'variant-filled-primary' : ''}"
			role="tab"
			aria-selected={value === opt.id}
			data-testid={opt.testid}
			on:click={() => dispatch('change', { id: opt.id })}
		>
			{opt.label}
		</button>
	{/each}
</div>
