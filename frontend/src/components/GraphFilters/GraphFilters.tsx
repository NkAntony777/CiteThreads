import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Slider, Select, Space, Button, Typography } from 'antd';
import { FilterOutlined, ReloadOutlined } from '@ant-design/icons';
import { useGraphStore } from '../../stores/graphStore';
import type { CitationIntent } from '../../types';
import './GraphFilters.css';

const { Text } = Typography;

const INTENT_OPTIONS: { value: CitationIntent; label: string }[] = [
    { value: 'SUPPORT', label: 'SUPPORT' },
    { value: 'OPPOSE', label: 'OPPOSE' },
    { value: 'NEUTRAL', label: 'NEUTRAL' },
    { value: 'UNKNOWN', label: 'UNKNOWN' },
];

export const GraphFilters: React.FC = () => {
    const { t } = useTranslation();
    const {
        nodes,
        yearRange,
        intentFilter,
        setYearRange,
        setIntentFilter,
    } = useGraphStore();

    const yearExtent = useMemo(() => {
        const years = nodes
            .map(node => node.year)
            .filter((year): year is number => typeof year === 'number');
        if (years.length === 0) return null;
        return [Math.min(...years), Math.max(...years)] as [number, number];
    }, [nodes]);

    const sliderValue = yearRange ?? yearExtent ?? [0, 0];
    const hasFilters = yearRange !== null || intentFilter.length > 0;

    return (
        <Card
            size="small"
            className="graph-filters"
            title={
                <Space>
                    <FilterOutlined />
                    <span>{t('graphFilters.title')}</span>
                </Space>
            }
            extra={
                <Button
                    type="text"
                    size="small"
                    icon={<ReloadOutlined />}
                    disabled={!hasFilters}
                    onClick={() => {
                        setYearRange(null);
                        setIntentFilter([]);
                    }}
                >
                    {t('common.reset')}
                </Button>
            }
        >
            <div className="filter-section">
                <Text type="secondary">{t('graphFilters.yearRange')}</Text>
                <Slider
                    range
                    min={yearExtent ? yearExtent[0] : 0}
                    max={yearExtent ? yearExtent[1] : 0}
                    value={sliderValue}
                    onChange={(value) => setYearRange(value as [number, number])}
                    disabled={!yearExtent}
                />
                {!yearExtent && <div className="filter-hint">{t('graphFilters.noYearData')}</div>}
            </div>

            <div className="filter-section">
                <Text type="secondary">{t('graphFilters.citationIntent')}</Text>
                <Select
                    mode="multiple"
                    allowClear
                    placeholder={t('graphFilters.allIntents')}
                    options={INTENT_OPTIONS}
                    value={intentFilter}
                    onChange={(values) => setIntentFilter(values as CitationIntent[])}
                    style={{ width: '100%' }}
                />
            </div>
        </Card>
    );
};
