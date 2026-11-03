import { Children, type ComponentProps, type ReactNode } from 'react';
import { Text, type TextProps } from 'react-native';
import Animated from 'react-native-reanimated';

import { Brand } from '@/constants/brand';
import { fontFor, lineHeightFor, splitByScript, type TypeRole } from '@/constants/typography';

// The only text component screens should use. Pick a role from the type
// scale (`variant`), and mixed-script strings such as "ಕನ್ನಡ · हिंदी · English"
// are split automatically so every run is set in the matching Baloo cut.

type Props = TextProps & {
  variant?: TypeRole;
  color?: string;
  align?: 'left' | 'center' | 'right';
  children?: ReactNode;
};

function renderRuns(children: ReactNode, variant: TypeRole): ReactNode {
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

function baseStyle(variant: TypeRole, color: string | undefined, align: Props['align'], children: ReactNode) {
  return [fontFor(variant), { color: color ?? Brand.ink, textAlign: align, lineHeight: paragraphLineHeight(children, variant) }];
}

export function AppText({ variant = 'body', color, align, style, children, ...rest }: Props) {
  return (
    <Text maxFontSizeMultiplier={1.5} {...rest} style={[baseStyle(variant, color, align, children), style]}>
      {renderRuns(children, variant)}
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
    <Animated.Text maxFontSizeMultiplier={1.5} {...rest} style={[baseStyle(variant, color, align, children), style]}>
      {renderRuns(children, variant)}
    </Animated.Text>
  );
}
