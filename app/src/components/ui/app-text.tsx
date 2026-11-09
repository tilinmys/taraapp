import { Children, type ComponentProps, type ReactNode } from 'react';
import { Text, type TextProps } from 'react-native';
import Animated from 'react-native-reanimated';

import { Brand } from '@/constants/brand';
import { fontFor, lineHeightFor, splitByScript, type Script, type TypeRole } from '@/constants/typography';

// The only text component screens should use. Pick a role from the type
// scale (`variant`), and mixed-script strings such as "ಕನ್ನಡ · हिंदी · English"
// are split automatically so every run is set in the matching Baloo cut.

type Props = TextProps & {
  variant?: TypeRole;
  color?: string;
  align?: 'left' | 'center' | 'right';
  children?: ReactNode;
};

/**
 * The one script of a text that is all Kannada, all Devanagari or all Latin
 * (null when mixed or not plain text). Such a text gets its font on the
 * outer <Text> itself: no nested span, so Android can wrap it and shrink it
 * (adjustsFontSizeToFit ignores the fixed size of a nested span there).
 */
function singleScript(children: ReactNode): Script | null {
  let script: Script | null = null;
  let ok = true;
  Children.forEach(children, (child) => {
    if (!ok) return;
    if (typeof child !== 'string') {
      if (typeof child !== 'number' && child != null && child !== false) ok = false;
      return;
    }
    for (const run of splitByScript(child)) {
      if (script && run.script !== script) ok = false;
      script = run.script;
    }
  });
  return ok ? script : null;
}

function renderRuns(children: ReactNode, variant: TypeRole, single: Script | null): ReactNode {
  if (single) return children;
  return Children.map(children, (child) => {
    if (typeof child !== 'string') return child;
    const runs = splitByScript(child);
    if (runs.length === 1 && runs[0].script === 'latin') return child;
    return runs.map((run, i) => (
      <Text key={i} style={fontFor(variant, run.script)}>
        {run.text}
      </Text>
    ));
  });
}

/** Line height for the whole paragraph: its tallest script's (Kannada needs
 *  more room than Latin; the paragraph's own line height wins on both platforms). */
function paragraphLineHeight(children: ReactNode, variant: TypeRole): number {
  let tallest = lineHeightFor(variant);
  Children.forEach(children, (child) => {
    if (typeof child !== 'string') return;
    for (const run of splitByScript(child)) tallest = Math.max(tallest, lineHeightFor(variant, run.script));
  });
  return tallest;
}

function baseStyle(variant: TypeRole, color: string | undefined, align: Props['align'], children: ReactNode, single: Script | null) {
  return [fontFor(variant, single ?? 'latin'), { color: color ?? Brand.ink, textAlign: align, lineHeight: paragraphLineHeight(children, variant) }];
}

export function AppText({ variant = 'body', color, align, style, children, ...rest }: Props) {
  const single = singleScript(children);
  return (
    <Text maxFontSizeMultiplier={1.5} {...rest} style={[baseStyle(variant, color, align, children, single), style]}>
      {renderRuns(children, variant, single)}
    </Text>
  );
}

/** Same as AppText, for Reanimated styles (entrances, morphs). */
export function AnimatedAppText({
  variant = 'body',
  color,
  align,
  style,
  children,
  ...rest
}: Omit<ComponentProps<typeof Animated.Text>, 'children'> & {
  variant?: TypeRole;
  color?: string;
  align?: 'left' | 'center' | 'right';
  children?: ReactNode;
}) {
  return (
    <Animated.Text maxFontSizeMultiplier={1.5} {...rest} style={[baseStyle(variant, color, align, children, singleScript(children)), style]}>
      {renderRuns(children, variant, singleScript(children))}
    </Animated.Text>
  );
}
