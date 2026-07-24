<script lang="ts">
	import { m } from '$lib/paraglide/messages';
	import type { VerdictView } from './types';

	export let verdict: VerdictView;

	$: pct = verdict.confidence !== null ? Math.round(verdict.confidence * 100) : null;
</script>

<div class="card p-4" data-testid="verdict-panel">
	<h3 class="h4 mb-2">{m.replay_verdict_title()}</h3>
	<div class="flex items-center gap-3 mb-2">
		<span
			class="badge {verdict.flippedTo
				? 'variant-soft-warning'
				: 'variant-soft-success'} font-mono uppercase"
			data-testid="verdict-decision"
		>
			{#if verdict.flippedTo}
				<s class="opacity-70 mr-1">{verdict.decision}</s>
				{verdict.flippedTo}
			{:else}
				{verdict.decision ?? '…'}
			{/if}
		</span>
		{#if pct !== null}
			<div class="confbar" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
				<div
					class="conffill {verdict.flippedTo ? 'flip' : ''}"
					style={`width: ${pct}%`}
				/>
			</div>
			<span class="text-xs font-mono opacity-70">{pct}%</span>
		{/if}
	</div>
	{#if verdict.keyEvidence.length > 0}
		<ul class="ev">
			{#each verdict.keyEvidence as line}
				<li>{line}</li>
			{/each}
			{#each verdict.gaps as gap}
				<li class="gap">{gap}</li>
			{/each}
		</ul>
	{/if}
	{#if verdict.recommendation}
		<p class="text-xs opacity-70 mt-2">{verdict.recommendation}</p>
	{/if}
</div>

<style>
	.confbar {
		flex: 1;
		height: 0.6rem;
		border-radius: 0.25rem;
		background: rgb(var(--color-surface-700) / 0.7);
		overflow: hidden;
	}
	.conffill {
		height: 100%;
		background: rgb(var(--color-primary-500));
		transition: width 0.6s ease;
	}
	.conffill.flip {
		background: rgb(var(--color-warning-500));
	}
	.ev {
		list-style: none;
		margin: 0;
		padding: 0;
		font-size: 0.75rem;
		opacity: 0.85;
	}
	.ev li {
		padding: 0.2rem 0 0.2rem 1rem;
		position: relative;
	}
	.ev li::before {
		content: '›';
		position: absolute;
		left: 0.1rem;
		opacity: 0.5;
	}
	.ev li.gap::before {
		content: '?';
		color: rgb(var(--color-warning-500));
	}
	@media (prefers-reduced-motion: reduce) {
		.conffill {
			transition: none;
		}
	}
</style>
