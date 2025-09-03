import React, {useRef,useEffect} from 'react';
import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';
import useBaseUrl from '@docusaurus/useBaseUrl';

type FrameTabProps = {
    src: string;
    label: string;
};

const frameStyle: React.CSSProperties = {
    width: '100%',
    height: '500px',
    border: '1px solid #ccc',
    borderRadius: '6px',
    marginBottom: '1em',
    overflow: 'auto',
};

const FrameTab: React.FC<FrameTabProps> = ({ src, label }) => {
    const iframeRef = useRef<HTMLIFrameElement>(null);
    const url = useBaseUrl(src);

    useEffect(() => {
        const iframe = iframeRef.current;
        if (!iframe) return;

        const onLoad = () => {
            try {
                const doc = iframe.contentDocument || iframe.contentWindow?.document;
                if (!doc) return;

                const elementsToRemove = doc.querySelectorAll(
                    'div[data-testid="content-spacer"], div[data-testid="extra-whitespace"]'
                );

                elementsToRemove.forEach(el => {
                    if (!el.children.length) {
                        el.remove();
                    }
                });
            } catch (err) {
                console.warn(err);
            }
        };

        iframe.addEventListener('load', onLoad);

        return () => {
            iframe.removeEventListener('load', onLoad);
        };
    }, [url]);

    return (
        <div>
            <div style={{ marginBottom: '0.5em' }}>
                <a href={url} target="_blank" rel="noopener noreferrer">
                    Open notebook in new tab
                </a>
            </div>
            <iframe ref={iframeRef} src={url} style={frameStyle} title={label} />
        </div>
    );
};

const LakebridgeTabs: React.FC = () => (
    <Tabs>
        {/*        <TabItem value="Readme" label="Readme" default>
            <FrameTab src="/lakebridge_reconcile/Readme.html" label="Readme" />
        </TabItem>*/}
        <TabItem value="Recon Main" label="Recon Main">
            <FrameTab src="/lakebridge_reconcile/lakebridge_recon_main.html" label="Recon Main" />
        </TabItem>
        <TabItem value="Recon Wrapper" label="Recon Wrapper">
            <FrameTab src="/lakebridge_reconcile/recon_wrapper_nb.html" label="Recon Wrapper" />
        </TabItem>
        <TabItem value="Snowflake Example" label="Transformation Query Generator">
            <FrameTab
                src="/lakebridge_reconcile/snowflake_transformation_query_generator.html"
                label="Query Generator"
            />
        </TabItem>
    </Tabs>
);

export default LakebridgeTabs;
